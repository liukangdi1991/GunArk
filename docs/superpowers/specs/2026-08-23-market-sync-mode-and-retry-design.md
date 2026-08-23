# 行情同步：模式公告与失败重试 设计文档

**日期**: 2026-08-23
**状态**: 已评审（两轮后端 review，无遗留问题）
**范围**: `trendradar/infrastructure/tushare/syncer.py`、`trendradar/app/services/market_service.py`、相关测试

---

## 1. 背景与问题

首次全量同步后，用户看到的结果摘要晦涩且失败无兜底：

1. **模式不透明**：`Sync complete: mode=init, missing_days=2828, synced_days=0, failed_codes=162` —— `init`/`incremental` 内部值用户无法理解；`missing_days`/`synced_days` 含义混淆（实测一次全量同步 `synced_days=0` 属预期，但不可读）；**模式决策发生在同步内部，开始前无任何提示**。
2. **失败无重试**：`failed_codes=162` 只写入 `sync_retry_codes.json`，需要用户手动再跑一次才补；失败原因（Tushare `daily` 限频 300 次/分钟）没有系统性兜底。
3. **限流根因缺陷**：
   - `_fetch_with_retry` 内部最多 3 次重试**不消耗 token bucket**（`bucket.acquire()` 只在 `sync_by_stock` 外层调用一次），实际 API 速率可达 3×270/min，撞破 300/min 上限。
   - 限频错误消息 `频率超限(300次/分钟)` 不含 `IP_BAN_ERROR_MSG` 标记，走通用重试路径，退避仅 1s/2s/4s，远小于 Tushare 的 1 分钟窗口 —— 限频状态下重试必再失败，且"被限 → 重试 → 更多请求 → 更限"自激级联。

## 2. 需求（用户澄清结论）

1. 同步模式明确标注为 **全量模式 / 增量模式**，且**在同步一开始**就提示（含原因：数据缺口 > 20 天启用全量同步）。
2. 模式内部值：`init` **改名为 `full`**（`incremental` 不变）；历史 job 记录中的 `init` 不做兼容映射（用户接受）。
3. **失败代码重试**：全部同步完成后，失败的 codes 继续重试，直到没有失败，或**总共同步 10 次**（初始 1 次 + 最多 9 轮重试）仍有失败才停止；轮间间隔 **30 秒**。
4. 顺带修复限流根因（重试消耗 token + 限频错误长退避/放弃策略）。

## 3. 设计

### 3.1 `plan_sync` 纯函数（单一决策源）

```python
@dataclass(frozen=True)
class SyncPlan:
    mode: str            # "full" | "incremental"
    missing_days: int    # 日历缺口（交易日）
    start: date
    end: date
    uptodate: bool       # 已是最新（is_up_to_date 短路）
    force: bool
```

决策规则（与现状行为一致，仅抽出为纯函数）：

```
retry_codes 非空（sync_retry_codes.json 有残留失败）→ mode="full"（by-stock 子集路径）
force 或 missing_days > 20                        → mode="full"
否则                                               → mode="incremental"
is_up_to_date 短路                                → uptodate=True（跳过同步）
```

**约束**：`sync_market` 接收 `SyncPlan` 并直接使用 `plan.mode`/`plan.missing_days`/`plan.uptodate`，**内部不再重算**（避免双源不一致）。

### 3.2 模式公告（同步开始前）

`market_service.submit_market_sync` 的 worker 在调用 `sync_market` 前，根据 `plan` 输出：

```
[INFO] 数据缺口 N 天 > 20 天，启用全量同步（按股票拉取全历史）   # full
[INFO] 数据缺口 N 天 ≤ 20 天，启用增量同步（按日拉取）            # incremental
[INFO] 行情已是最新，跳过同步                                      # uptodate
[INFO] 存在 X 个失败代码待补，进入全量重试模式                     # retry_codes 触发 full
```

### 3.3 重试循环（收在 `sync_market` 编排点）

```
主同步执行完成
while failed_codes > 0 and rounds < 9:
    rounds += 1
    公告: [INFO] 第 N/9 轮重试：X 个失败代码，30 秒后开始
    可取消 sleep(30)（1s 粒度循环 + cancel_check）
    本轮 = sync_by_stock(仅上一轮失败的 codes)   # 复用 load_retry_codes 机制
    更新累计 synced_codes；failed_codes = 本轮剩余
    if failed_codes == 0: 公告 [INFO] 全部失败代码已补完; break
```

规则：
- **共 10 次请求批**：初始 1 次 + 最多 9 轮重试。
- 每轮只同步**上一轮失败的 codes**（`sync_retry_codes.json` 子集）。
- 任一轮 `failed_codes == 0` → 提前终止。
- **适用范围**：重试循环仅当执行的路径产生 `failed_codes`（full/by-stock 路径）时启动。incremental（日路径）失败的是"天"（`failed_days`），保持现状（留待下次同步），不进入重试循环。
- 取消：主同步或任一轮中取消 → 立即停止；30s 间隔可被取消中断。
- 重试轮内 progress 回调 `msg` 前缀 `[重试 N/9]`。

### 3.4 限流修复

1. `_fetch_with_retry` 增加 `bucket` 参数：**每次尝试（含重试）前 `bucket.acquire(timeout=60, cancel_check=...)`**；acquire 超时或取消 → 返回 `None`（记为失败代码），不无限阻塞。
2. `sync_by_stock` 与 daily 路径的外层 acquire 移除，统一收进 fetch 函数（避免双重计数）。
3. 限频错误处理：
   - 消息含 **`频率超限`** → **本轮放弃**（返回 `None` → 计入失败 codes → 交重试轮 30s 后处理）。不做 1s/2s/4s 立即重试（避免多 worker 同时退避后再次突发撞墙）。
   - 消息含 `IP_BAN_ERROR_MSG` → 保持 600s 冷却后重试（更严重的封禁信号）。
   - 其他异常 → 保留现有 `2^attempt` 退避重试（最多 3 次）。

### 3.5 结果字段

```
mode:            "full" | "incremental"        # 原 init → full
missing_days:    int                            # 日历缺口（规划指标）
synced_days:     int                            # 日路径实际合成天数（full 模式下恒为 0）
synced_codes:    int                            # 累计成功代码数（主同步 + 各轮重试）
new_codes:       int                            # 新增 parquet 数（日路径）
failed_days:     int                            # 日路径失败天数
failed_codes:    int                            # 最终剩余失败代码数（by-stock 语义；日路径读取 retry 文件）
retry_rounds:    int                            # 新增：实际重试轮数
skipped_uptodate: bool
```

`market_service` worker 的完成日志同步更新：

```
Sync complete: mode=full, missing_days=2828, synced_codes=5549, failed_codes=0, retry_rounds=2
```

## 4. 边界与错误处理

| 场景 | 行为 |
|---|---|
| 主同步 0 失败 | `retry_rounds=0`，无重试 |
| 9 轮后仍有失败 | 停止，`failed_codes` 保留最终值，下次同步自动补 |
| 同步中途取消 | 立即停止，结果如实记录已完成的计数 |
| acquire 超时/取消 | 该代码记为失败，不阻塞作业 |
| 重试轮内再次限频 | 本轮放弃 → 下一轮继续（收敛） |
| 历史 job 记录 `mode=init` | 透传展示，不做兼容映射 |

## 5. 测试计划（TDD）

**更新**（断言 `mode="init"` → `"full"`）：`tests/infrastructure/test_sync_market.py`、`test_market_service_incremental.py`、`test_sync_{planner,daily,by_stock,integration}.py` 中相关断言、`tests/interfaces/test_api_contract.py`（如涉及）。

**新增**：
- `plan_sync` 决策：缺口 >20 / ≤20 / force / retry_codes 非空 / uptodate 短路。
- 重试循环：mock 失败→重试→成功（`retry_rounds` 正确）；9 轮上限；间隔注入 0（`retry_interval=0`）；重试轮取消。
- 限流修复：重试消耗 token（bucket 计数）；`频率超限` 放弃不重试；IP_BAN 600s 冷却路径；acquire 超时返回 None。
- 字段语义：full 模式 `synced_days=0`；`synced_codes` 累计；`retry_rounds` 存在。

## 6. 非目标

- 前端 MarketDataPage 的模式徽章/统计卡片（仅透传 console 文本；前端无 mode 硬编码则零改动）。
- 日路径（incremental）失败天的自动重试。
- 多机/分布式同步。

## 7. 影响面

- 文件：`syncer.py`（plan_sync、重试循环、限流修复、字段）、`market_service.py`（公告日志、完成日志）、测试若干。
- 前端：无硬编码（实施时核实 `frontend/src/services/marketData.ts`）。
- 存量数据：历史 `mode=init` 记录不兼容（已接受）。
