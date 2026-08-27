# 行情数据地基：同步完成性修复与状态面板 设计文档

> **本文已作废（2026-08-27）**：讨论中确认「在既有 plan_sync/decide_mode 状态机上打补丁」无法同时解决请求污染日历、假绿、越权打勾三类结构性缺陷，改为整体重做同步机制。最新设计见 `2026-08-27-market-sync-redesign-design.md`。本文仅保留作问题背景与现状取证参考。

**日期**: 2026-08-27
**状态**: 已废弃（superseded）
**范围**: `trendradar/infrastructure/tushare/syncer.py`、`trendradar/app/services/market_service.py`、`trendradar/infrastructure/storage/schema.py`、`trendradar/interfaces/api/presenters.py`、`frontend/src/pages/MarketData/MarketDataPage.tsx`、`frontend/src/types/marketData.ts`、相关测试

---

## 1. 背景与问题

行情数据是选股与回测的地基，但当前"同步是否真的完成了"**不可信且不可见**。代码复核确认四类缺陷：

1. **空响应吞日**（`syncer.py` 按日路径）：历史交易日 `pro.daily(trade_date=)` 返回空表时，代码当作"确认无数据"**直接标 done**，只有 `plan.latest` 当天享受重试待遇。若空响应实为数据方发布延迟，该交易日永久缺失且无任何痕迹。
2. **越权标 done**（`sync_by_stock` 的全有或全无打勾，`syncer.py:490-497`）：`done` 是"日维度全市场"标记，但以下三类**只拉了部分股票**的批次成功后也会给整段日期打勾：
   - 按日路径里的"新上市补齐"批（只同步新代码）；
   - 欠账本（`sync_retry_codes.json`）非空时，`sync_by_stock` 入口用队列**顶替**全市场清单（`codes = load_retry_codes(retry_path) or codes`），只拉队列子集却给整段日期打勾——其余股票的该段日期永久缺失；
   - 用户经 API 直传 `codes` 的指定子集批。
3. **取消悬空**（`sync_by_stock` 的 as_completed 循环）：取消时 `break`，未轮到/未收割结果的代码既不进欠账本也不阻止打勾——若已收割部分恰好零失败，走 else 分支给整段日期打勾；欠账本只记"已失败"，被取消的无人认领。
4. **同步结果不可见**：`market_sync_runs` 表 `empty_count`/`stock_count` 恒 0（backlog #8 核实），且缺 `mode`/`synced_days` 等列；行情数据页只显示文件扫描出的 4 指标，回答不了"我现在的数据新不新、齐不齐、上次同步怎么样了"。

**用户决策**：`empty_count`/`stock_count` 这类计数没有意义，要的是**空响应不被吞、打勾不越权、取消不悬空**；面板只保留硬信息。附带确认的代价：子集批修复后缺口日留在清单、下轮按日全市场重补——**宁可多拉，不可假齐**。

## 2. 需求

- R1 空响应（含最新日与历史日）一律**不标 done**，下轮同步自动重试；明细写执行日志。
- R2 `done` 打勾权只授予"本批次确实覆盖全市场（按股票批）或全市场当日（按日批）"的场景；三类越权路径全部关闭。
- R3 取消时：本次批内**所有未成功完成**的代码并入欠账本；被取消的运行**绝不**打勾。
- R4 `GET /api/market-data/status` 新增 5 个字段（见 §4），行情数据页按已选定的 **C 布局**（左 2×2 指标 + 右"数据地基"面板）展示。
- R5 `market_sync_runs` 表废弃（停写、删 DDL、删死函数 `market_service.get_market_status()`）；#8 随表了结。
- R6 同步作业结束（无论成败）后失效状态缓存，面板即时反映新状态。

## 3. 设计：同步可靠性（A1–A3）

### 3.1 职责重划：markers 归编排层所有
wwwwww
`sync_by_stock` 退化为**纯抓取合并单元**：不再读写 `sync_done.json` / `sync_retry_codes.json`，返回扩展为：

```python
def sync_by_stock(...) -> dict:
    # {"failed_codes": [本轮未成功完成的代码，含被取消未跑到的],
    #  "cancelled": bool}
```

参数表同步移除 `done_path` / `retry_path`（它不再碰 marker）。两个 marker 文件由 `sync_market` 统一维护，这是 R2/R3 能钉死的前提——打勾权收归唯一编排点。

### 3.2 欠账本不变量

每轮同步结束时（含取消）：

```
新队列 = (旧队列 ∪ 本轮未成功完成的代码) − 本轮确认完成的代码
```

- 全市场按股票批：轮次重试链（现有 9 轮机制不动）跑完后，残余 failed 即新队列。
- 批次清单选择规则（钉死，替代现状"队列无条件顶替入参清单"）：请求带 `codes` → 只拉 `codes`，旧队列原样保留（按公式它不会被完成，自动滚存）；无 `codes` 且队列非空 → 只拉队列子集；无 `codes` 且队列为空 → 全市场清单。
- 新上市补齐批 / 取消：其 failed **并入**队列，**永不清空**队列（现状 bootstrap 成功会误清整个队列，随职责移交自然消除）。
- 只有"全市场批零失败收尾"才把队列清空（`save_retry_codes(path, [])`），语义：欠账全部划销。

### 3.3 打勾规则（唯一写法）

| 路径 | 打勾条件 | 打什么 |
|---|---|---|
| 按日增量 | 该日拉取成功（响应非空）且合并落盘完成 | 该日（现状逐日语义不变，天然全市场） |
| 按日增量·空响应 | **不打**（R1，废除"历史日标 done"特例；`day == plan.latest` 分支与之合并） | — |
| 按股票 full（全市场批：无 req_codes 且规划时队列为空） | 全部轮次结束、零失败、未取消 | `plan.start..plan.end` 的交易日（全有或全无，现状语义保留） |
| 按股票（队列子集批 / 用户指定 codes 批 / 新上市批） | **永不**打勾 | — |
| 任何被取消的运行 | **绝不**打勾 | — |

队列子集批修复后的闭环（已在设计中显式接受）：子集批不打勾 → 缺口日仍在 `missing_dates` → 若 ≤20 天，下轮走按日路径全市场补齐；新上市批同理。多花的请求是**故意的**。

### 3.4 取消收割

`sync_by_stock` 内取消不再 `break` 丢弃结果：置 `cancelled=True` 后继续收割**全部** futures（`fetch_one` 入口已有 cancel 短路，未跑到的会立即返回 False），全部非 True 结果计入 `failed_codes` 返回。落盘原语 `_atomic_write_parquet` 失败仍向上抛出（现状，job 失败、该日不打勾，语义不变）。

## 4. 设计：状态接口

### 4.1 契约（现有 5 字段一字不改，向后兼容）

`GET /api/market-data/status` 新增：

```jsonc
{
  // —— 现有：data_dir / stocklist / stock_count / local_file_count / latest_date ——
  "authoritative_latest": "2026-08-27",   // 日历 + 16:00 规则；复用 latest_tradeable_day(set, now_utc)
  "stale_days": 4,                        // 交易日数 ∈ (latest_date, authoritative_latest]；latest_date 为 null 时 null
  "marker_mismatch": false,               // done 声称覆盖到 authoritative_latest 但 stale_days > 0
  "retry_pending": 2,                     // len(load_retry_codes(...))
  "last_sync": {                          // jobs 表 job_type='market_sync' 最新一行；无则 null
    "job_id": "20260827_180401_market_sync_ab12",
    "status": "success",                  // queued|running|success|failed（含取消后的 failed）
    "started_at": "2026-08-27 10:04:01",  // UTC ISO，与现有 jobs 时间戳同制式
    "finished_at": "2026-08-27 10:09:33",
    "error_message": null,
    "console_url": "/console/20260827_180401_market_sync_ab12"
  }
}
```

### 4.2 数据源与口径

- 全部新增计算落在 `presenters._compute_market_status()`，随现有 30s TTL 缓存。
- 权威日历：`load_trade_calendar(cache_dir / "trade_calendar.parquet")`（`infrastructure/tushare/calendar.py` 现成函数）；返回 None（从未同步过）时 `authoritative_latest`/`stale_days`/`marker_mismatch` 均为 null/false，面板降级显示"日历未就绪"。
- `stale_days` 基于**文件实测**的 `latest_date`（不信任 done），这是面板主数字可信性的来源。
- `marker_mismatch` = `done` 非空 且 `max(done) >= authoritative_latest` 且 `stale_days > 0`。A1–A3 修复后仍保留，作为静默缺口的兜底警报。
- `last_sync` 一条 `SELECT ... FROM jobs WHERE job_type='market_sync' ORDER BY id DESC LIMIT 1`，读法与 `job persistence` 现成表，无新表无迁移。
- **不做**：任何全库扫描校验、per-stock 覆盖对账（§6）。

### 4.3 缓存失效（R6）

`presenters` 暴露 `invalidate_market_status_cache()`；`market_service.submit_market_sync` 的 worker 以 `try/finally` 在**任何终态**（成功/失败/取消/异常）后调用。

## 5. 设计：清理（#8 了结）

- `schema.py`：删除 `market_sync_runs` 表 DDL 与索引（老库残留表不 DROP，孤儿无害）。
- `market_service._register_market_sync_metadata`：删除 INSERT，仅保留 `register_execution` 调用（函数随之瘦身更名 `register_market_sync_execution`）。
- `market_service.get_market_status()`：死函数删除（全仓无调用方，已核实）。
- `frontend/src/types/marketData.ts`：`TradingDatesResponse` 删除后端从不返回的幽灵字段 `effective_to` / `latest_data_date`。

## 6. 前端：C 布局

顶部一个 `Row`：左 `Col`（`xs=24 lg=14`）内放**现有 4 指标卡**改 2×2（内容一字不改）；右 `Col`（`xs=24 lg=10`）新增"数据地基" Card，行序：

```
数据新鲜度    落后 4 个交易日            ← stale_days：0 绿 / >0 红 / null 灰"日历未就绪"
              最近可交易日 08-27 · 本地最新 08-21   ← authoritative_latest + latest_date 小字
⚠ 标记与实际不一致，建议强制重拉        ← marker_mismatch=true 才出现的 Alert（warning 色）
最近一次同步  08-27 18:04 · 成功 · 查看控制台 →    ← last_sync；null 时"暂无同步记录"
欠账本        待重试 2 只（琥珀色）/ 无欠账（灰）  ← retry_pending
```

"本地存储"卡与拉取表单行保持现状不动。控制台链接为 `<a href>`（同 SPA 路由）。

## 7. 测试计划（TDD，基线 378）

**RED 先行，逐条对应需求：**

- R1：mock 空响应的**历史日** → 断言该日不入 done、日志含"空响应…将重试"；同数据再跑一轮 → 该日重新出现在拉取清单。
- R2：三个用例——新上市批成功、队列子集批成功、用户指定 codes 批成功——各断言 `sync_done.json` 无新增；对照组：全市场批零失败照常打勾（防修过头）。
- R3：`sync_by_stock` 跑到一半 cancel → 断言返回的 `failed_codes` = 未完成全集、`cancelled=True`；编排层断言队列收到并集、done 无新增；断言 bootstrap 成功**不清空**既有队列。
- §3.1 重构波及的现有测试（`test_sync_by_stock.py` 中 marker 断言）随新职责改写。
- §4：presenter 单测（tmp runtime root 造 parquet 日历/文件/队列/`jobs` 行夹具）断言 5 新字段各态（含日历缺失降级、`last_sync=null`）+ `test_api_contract.py` 契约用例。
- §4.3：worker 终态后缓存被清（可直接断言 `presenters._market_status_cache["at"]` 归零或 payload 清空）。
- §5：`test_schema.py` 删 `market_sync_runs` 断言；metadata 相关测试改断言不再 INSERT 该表。
- 前端：`tsc` + `vite build` 通过；uvicorn 冒烟看板上五行真实数据（含手动制造一次取消）。

## 8. 明确不做（YAGNI 边界）

- `empty_count`/`stock_count` 类计数字段（表已废弃）。
- 每只股票逐日覆盖完整性扫描（5211 文件对账；force 重拉兜底）。
- 停牌股语义变更：`done` 仍是日维度"接口返回均已落盘"，不承诺个股每日一行。
- `exclude_boards` 与 done 的交互：排除板块的交易日照常打勾（用户主动不要这些板块，属产品选择）。
- 队列条目的入队时间/失败原因持久化（明细看各次执行日志即可）。

## 9. 风险与兼容性

- `sync_by_stock` 返回契约变化：仅 `sync_market` 一个生产调用方，测试同步改写；无跨层泄漏。
- 行为变更点共三处（空响应不打勾、子集批不打勾、取消并入队列），方向一致：**只会多拉不会少拉**，最坏代价是若干次重复请求。
- 存量库若已有历史越权缺口：本设计**不自动回溯发现**（§8 不做全库对账）；面板的 `stale_days` 只兜最新日期，历史洞需要用户主动 `force` 回填。可在 spec 实施后的一次性运维中建议 `force` 全量刷一遍（不进代码范围）。
