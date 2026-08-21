# TrendRadar 行情增量同步设计

> 版本：v5（三方评审 17 + 复审 12 + 二轮复审 8 全部吸收，2026-08-21）

## 目标

重构当前行情同步流程，使行情数据从「全量一次性拉取」演进为「初始化全量 + 例行增量」：初始化按股票多线程拉全历史，之后每次增量只拉本地缺失的交易日数据，并在新股上市时自动并入。所有同步仍通过 `market_data_sync` job 触发（手动），不引入定时调度。

## 命名约定（防漂移）

- **API 请求 `type` = `market_data_sync`**（前端分发层，presenters.py）。
- **内部 `job_type` / `execution_type` = `market_sync`**（market_service.py，现有测试与存量数据依赖此值）。
- 本次**不改名**，spec 内除 API 请求外一律称 `market_sync`。

## 背景与现状缺陷

当前 `sync_kline`（`trendradar/infrastructure/tushare/syncer.py`）：

```python
if _is_up_to_date(path, end):   # 只看本地最新日期 >= 请求 end
    skip
else:
    data = _fetch(start, end)   # 整段重拉
```

- 串行逐股票拉取：全量 5549 只 ≈ 90 分钟。
- **不是真正的增量**：`_is_up_to_date` 只比较 end 不比较 start——请求更早的起始日期时被判定「已最新」而跳过，**更早历史永不补拉**（实测发生：全量同步时 8 只被 skipped）。
- **并发互斥不存在**：`JobExecutor.submit()` 无 job_type 去重检查（max_workers=2，路由无防护），连续提交可并发两个 `market_sync`，导致同批 bars 文件「读-合并-写」交错（lost update）且 API 速率翻倍。
- **stock_meta 不随增量刷新**：`stock_meta()` 在文件存在时优先读文件不回退扫描 bars；增量创建的新股文件在下次全量 `sync_stock_list` 前对选股默认股票池不可见。

## Tushare API 约束（已确认事实）

| 约束 | 值 | 影响 |
|---|---|---|
| 单次调用行数上限 | 6000 行（基础积分档） | 单日全市场 ~5400 行 < 6000 可一次拉取；**单股票全历史可超 6000**（1990 上市老股约 8000+ 交易日）→ 初始化必须分片 |
| 每分钟调用次数 | 500 次/分（基础积分档） | 全局限速基准；实测全量 ~61 次/分远未触及 |
| 官方推荐 | 「循环日期提取全市场，不要循环 ts_code」 | 增量按交易日循环；初始化按股票分片（受 6000 行约束） |
| 数据入库时点 | 交易日 15:00～16:00 之间入库 | 当日日 K 在 16:00（**北京时间**）后可用；时钟用 `datetime.now(ZoneInfo("Asia/Shanghai"))` 钉死，不依赖进程本地时区 |
| 历史深度 | 覆盖 A 股上市以来全部历史（1990 起） | 无深度限制 |
| 单日返回量 | ~5400 行，逼近 6000 上限 | 返回 >5700 行记 warning；设计降级（按交易所/代码前缀分批）预案 |

## 已确认决策

- 初始化（空库/新部署）：按股票轮询 + 多线程拉全历史；**每股票按行数分片**（每片 <6000 行）拉取后合并。
- 增量：按缺失交易日循环，`daily(trade_date=...)` 一次全市场；每个缺失日 1 次调用。
- **分派规则**：`bars/` 为空 → 初始化全量；否则 → 增量。**缺失交易日数 > 阈值（默认 20 天）→ 自动改走按股票拉取/合并路径**（复用初始化多线程），否则按日增量。
  - 阈值依据：按日路径每缺失日需读改写 ~5200 个文件（读-合并-写），20 日 ≈ 10 万次文件操作；按股票路径约 8000 次 API 调用 + 每股票 1 次原子写。缺口大时按股票路径显著更优。阈值可配置（环境变量 `SYNC_INCREMENTAL_DAY_THRESHOLD`）。
- **增量默认 `start` = 本地最早日期**（不默认 90 天前）。**API 契约变更说明**：`submit_market_sync` 的 `start_date` 缺失时默认值从 `today-90d` 改为本地 bars 最早日期；前端总是显式发送（MarketDataPage 默认 2019-01-01），不受影响；直接 API 调用者不指定 start_date 时，首次运行可能触发大范围补拉（缺失日 >20 → 按股票路径，属「首次升级部署路径」预期行为）；**实现时在同步日志中提示实际使用的 start 值**。
- 触发：手动。新部署初始化先跑一次全量。
- 最新性判断：start 感知（见下）。
- 新股合并：按日/按股票拉取返回天然包含新股；写回无文件即创建；**每次同步刷新 `stock_meta.parquet`**。
- 不引入定时调度。

## 关键设计决策

### 1. 并发互斥（新增交付项）

`market_sync` 一次仅一个，**由代码强制而非假设**：

```text
权威判据 = JobExecutor 内存活跃集合（self._lock 临界区内原子完成：
  「检查活跃 market_sync + create_job + 登记内存状态」）—— 进程内无 TOCTOU
DB 查询（jobs 表 job_type='market_sync' AND status IN ('queued','running')）
  仅作重启兜底：lifespan 启动时执行恢复
  UPDATE jobs SET status='failed', error_message='interrupted by restart', finished_at=?
  WHERE job_type='market_sync' AND status IN ('queued','running');
  （kill/容器重启残留的 running 行不再永久 409）
路由层拒绝：活跃存在 → HTTP 409
```

前端实际入口是 `POST /api/executions` 分发（presenters），互斥检查必须落在 `JobExecutor.submit` 内（内存锁），路由 409 只是外圈提示。补测试：并发双提交仅一个成功。

### 2. 权威交易日历（trade_cal）设施

代码库无权威日历获取/缓存设施（本地 `calendar.parquet` 是 bars 日期并集，恰是增量要修补的对象，**不能作基准**）。新增：

```text
job 启动：pro.trade_cal(exchange='SSE', 覆盖请求区间) → 缓存到 storage/cache/trade_calendar.parquet
  - 成功：本次运行内复用（不重复调用）
  - 失败：用上次缓存（存在则继续，可降级但记 warning）
  - 两者皆无：job 直接失败（不降级为本地日历）
```

**两个日历文件的分工（防止混淆）**：

| 文件 | 来源 | 用途 |
|---|---|---|
| `storage/cache/trade_calendar.parquet` | Tushare `trade_cal`（权威，含未来） | **同步决策**：最新性判断、缺失交易日计算 |
| `storage/market/calendar.parquet` | bars 日期并集（数据驱动） | **选股/回测读取**：`get_calendar()` 等 |

互不替代：同步用权威日历决策「该拉哪些日」，读取用数据日历反映「实际有哪些日」。

### 2.1 请求契约（schema 变更）

`MarketSyncRequest` 增加 `force: bool = False`；`codes` 已有。

- **`force=true`**（API-only，前端 UI 不暴露）：忽略最新性判断与 sync_done 过滤，按请求区间重拉覆盖；成功日写 sync_done、失败日不写。**始终走按日路径，不参与 20 天阈值分派**（force 是精确修正场景，用户明确知道要重拉哪些日期，按日语义最匹配；按股票路径会拉每只股票全部历史、远超意图）。是「远端数据修正/本地自愈」的逃生通道。
- **显式 `codes`**：按股票模式执行（决策 7），**不写 sync_done**（子集同步不得标记为全市场完成）。
- **presenters 透传（具体位置）**：
  - `presenters.py::submit_execution_payload` 的 `market_data_sync` 分支（现只转发 start/end）改为转发 `codes` 与 `force`；
  - `market_service.py::submit_market_sync` 签名新增 `force: bool` 处理，透传给增量逻辑；
  - `routes/market.py` 的 `POST /api/market-data/sync` 经 `MarketSyncRequest`（新增 `force` 字段）自然携带。
- 两个提交端点（`POST /api/executions`、`POST /api/market-data/sync`）补契约测试：并发 409、force 重拉已标记日。

### 3. 最新性判断（快速通道，start 感知 + sync_done 校验）

```text
最近可交易日 = 今天（北京时间 >= 16:00 且 trade_cal 判定为交易日）否则上一交易日

廉价预判（max_date/min_date）：
  本地 max_date >= 最近可交易日
    且（请求未显式指定 start 或 请求 start >= 本地 min_date）→ 预判通过
  否则 → 直接进入缺失交易日计算

预判通过后仍须校验 sync_done（中间失败日可能被 max_date 掩盖）：
  缺失交易日 = trade_cal 区间 ∩ ≤最近可交易日 − sync_done
  缺失交易日 为空 → 已最新，结束（0 次行情调用）
  缺失交易日 非空 → 进入缺失交易日计算（即使 max_date 已达标）
```

注意：快速通道**不能只依赖 max_date/min_date**——某日失败后本地 max_date 仍会推进到最新，若跳过 sync_done 校验该失败日将永不重试。

### 4. 缺失交易日判定（完成标记，非日历并集）

日历并集口径下，某日部分写入后中断会被永久视为已覆盖（停牌缺口与失败缺口无法区分）。改用**按日完成标记**：

```text
storage/cache/sync_done.json：已完整同步成功的交易日清单（日期升序数组）
  - 原子写（.tmp + os.replace）；单 job 串行写（互斥已保证）
缺失交易日 = trade_cal 区间内交易日 − sync_done

按日模式（决策 6）：某日写入全部股票成功 → day 记入 sync_done；中断/失败 → 不记，下次重试。
按股票模式（初始化 §5 / 升级补缺 §「首次升级部署路径」）：
  「本批」= 本次运行处理的全部股票（初始化/升级补缺 = stock_meta 全量；显式 codes 子集走下方不写路径）
  全部成功 → 将 [start, end] 内全部交易日写入 sync_done
  部分失败 → 不写 sync_done（日维度语义保持「全市场完成」）；
            失败股票列表持久化到 storage/cache/sync_retry_codes.json
            下次运行缺失日仍 >20 时，按股票路径**只拉 retry 子集**（不再全量）
            retry 子集全部成功后 → 写 [start, end] 全部交易日入 sync_done，清空 retry 文件
  （避免「1 只持续失败 → 全区间永久反复按股票重拉」的活性问题；每轮成本 = 失败子集）
显式 codes 子集运行：**不写 sync_done**（只同步少数股票的日期不得标为全市场完成）
```

存量库（无 sync_done）首次运行：**一次性严格核对**——

```text
逐文件只读 date 列（并行，~1s）：
  全量覆盖日 = 出现在 所有 bars 文件 中的日期（交集，非并集）
  仅将全量覆盖日标入 sync_done（近似标记不产生）
曾失败/截断股票的空洞日、无文件股票的相关日不会被标记 → 按日增量会重拉（幂等）
遗留空洞（若仍存在）恢复手段：force=true 重拉

无文件股票的预期行为：其缺失日由后续增量按日路径逐日填充（每缺失日创建 1 行文件）；
缺失日 >20 后自动走按股票路径一次性补全；用户可 force=true 立即补全。
```

### 5. 初始化分片 + 全局限速

```text
每股票：按行数估算分片（每片 < 5500 行，6000 上限留余量）
  估算交易日数 ≈ (end - start).days / 7 * 5
  片数 = ceil(估算交易日数 / 5500)；每片日期跨度 = ceil(总天数 / 片数)
  [上市日, today] → 片1 [start, d1], 片2 [d1+1, d2], ... 逐片 daily(ts_code, start, end)
  → concat → 按 date 去重（新拉覆盖本地）→ 排序 → 原子写
  每片拉取前检查取消（分片粒度取消点）
全局限速器：TokenBucket（新模块 trendradar/infrastructure/tushare/rate_limit.py）
  capacity/refill = 450 次/分（500 上限留 10% 余量），控制提交节奏
N（线程数）只决定在途请求数（6-8），速率由令牌桶保证，不因网络变快而超限
pro 实例：驱动线程构建一次传入线程池（get_pro() 每次调用都写 token 文件，多线程各自调用会引入文件写竞争）
```

### 6. 增量按日循环

```text
end 统一钳制：end = min(end, 最近可交易日)（防未来交易日被空返回误标记）

for day in 缺失交易日（trade_cal 判定，均 ≤ 最近可交易日）:
    if ctx.check_cancelled(): break          # 每日迭代前检查；已完成日保留，剩余下次补齐
    df = pro.daily(trade_date=day)           # 一次全市场 ~5400 行
    if df 为空:
        若 day == 最近可交易日 → 不记 sync_done、记 warning、留待下次重试（当日数据可能未入库）
        否则 → 记入 sync_done 且 empty_count += 1（历史日空返回 = 异常休市/无数据，避免反复拉）
        continue
    for code, group in df.group_by("code"):  # 按 code 分发写回
        bars/{code}.parquet 不存在 → 创建（新股）
        存在 → 读本地 + concat → 按 date 去重（**新拉行覆盖本地同日行**）→ 排序 → 原子写回
    全部股票写入成功 → day 记入 sync_done
```

### 7. 请求契约（钉死语义）

- 去重 keep 策略：**新拉取行覆盖本地同日行**（历史数据以远端为准，不保留本地旧值）。
- `force` 逃生通道：请求带 `force=true` → 忽略最新性判断，按请求区间重拉覆盖（用于远端数据修正、本地损坏自愈）。
- 显式 `codes`：按股票模式执行（每日全市场拉取天然不适配指定 codes；初始化分片路径已具备按股票能力）。
- 显式 `end` = 今天且北京时间 <16:00：当日数据不可用，`end` 收敛为上一交易日（不产生空拉取）。

### 8. 新股合并 + stock_meta 刷新

```text
写回时 bars/{code}.parquet 不存在 → 创建（新股数据从上市日首个交易日开始）
stock_meta 刷新：**同步开始时无条件刷新一次**（保持现有行为，market_service 现状）
  理由：① daily 返回不含 name/industry，新股 name/industry 必须来自 stock_basic；
        ② 开始时刷新保证即使同步中途失败，stock_meta 也是最新的（新股对选股可见）；
        ③ 早退（已最新）场景同样刷新——新股可能在上一轮后上市（即使尚无行情数据）
  刷新必须原子写（.tmp + os.replace，改造 stocklist.py 现有直接 write_parquet 路径）
成本：每次同步（含早退）1 次 stock_basic 调用
```

### 9. 日历缓存一致性

- 增量逐日写文件会令 mtime 缓存逐日失效 → 同步窗口内任何读端触发全扫描。改为：
  - **同步完成后**显式原子重建 `calendar.parquet`（`.tmp` + `os.replace`）
  - 读端重建路径加锁（进程内锁），避免 `max_workers=2` 下并发重建竞争写坏缓存

### 10. 冷却与取消

- 限流冷却 `sleep(600)` 改为**可被 cancel_check 打断的分段等待**（每 5s 检查一次）。
- 共享冷却信号：并发下多线程触发限流时只冷却一次（其余线程等待同一信号），避免 N 个线程各睡满 600s 放大取消延迟。

## 数据模型变更（market_sync_runs）

现有列：`execution_key/start_date/end_date/stock_count/skipped_latest/empty_count/failed_count`（schema.py CREATE TABLE IF NOT EXISTS，**无迁移机制**——存量库不会获得新列）。

方案：**不改表结构**，新增同步统计写入 `jobs.result_json`（已有列，无迁移风险）：

```json
{
  "mode": "init" | "incremental",
  "missing_days": 396,
  "synced_days": 396,
  "synced_codes": 5549,
  "new_codes": 12,
  "failed_days": 0,
  "failed_codes": 0,
  "skipped_uptodate": true
}
```

`_days` 字段以「日」为维度；init 模式按股票组织，但 sync_done 仍按日标记（§4），取值约定：

| 字段 | init 模式 | incremental 模式 |
|---|---|---|
| missing_days / synced_days | 请求区间交易日数 / **写入 sync_done 的交易日数**（全部成功 = 请求区间交易日数；部分失败 = 0） | 缺失日数 / 成功日数 |
| synced_codes | 成功同步股票数 | 当日平均股票数 |
| new_codes | 本批新增股票数 | 新股数 |
| failed_days | 0 | 失败日数 |
| **failed_codes** | **失败股票数（持久化至 sync_retry_codes.json）** | 0 |

`market_sync_runs` 保留现有列与语义，各模式取值约定：

| 列 | init 模式 | incremental 模式 |
|---|---|---|
| start_date / end_date | 请求区间 | 缺失日区间 |
| stock_count | 同步股票数 | 当日平均股票数 |
| skipped_latest | 0 | 已最新跳过 = 1，否则 0 |
| empty_count | 无数据股票数 | 空返回日数 |
| failed_count | 失败股票数 | 失败日数 |

**reset 联动**：`init-v2 --reset-runtime` 的 `RESET_PATHS` 增加 `"cache/"`——`sync_done.json` 与 `trade_calendar.parquet` 同在 `storage/cache/`，reset 清空 bars 后必须一并清除完成标记，否则残留标记会掩盖新一轮 init 的失败分片日。

## 并发与一致性

- `market_sync` 并发互斥由代码强制（决策 1）。
- 多线程各写不同 code 文件，无写冲突；原子写（`.tmp` + `os.replace`）保证读方无半成品。
- 同股票「读-合并-写」在单线程内完成。
- 全局速率由令牌桶保证（决策 5），与线程数解耦。
- 共享 `pro` 实例（决策 5），避免 token 文件写竞争。

## 错误处理与可观测性

- 限流识别沿用（`每分钟最多访问该接口`），冷却改为可取消分段等待 + 共享信号（决策 10）。
- 单日拉取失败：该日**不记入 sync_done**，记 failed_days，下次增量自动重试（幂等）。
- 单股票分片某片失败：该股票不写（原子写保证旧文件完整），下次重试。
- 进度：初始化按股票 `current/total`；增量按交易日 `current/total`。
- 日志：result_json 记录 mode/missing_days/synced_days/new_codes/failed_days（决策「数据模型变更」）。

## 首次升级部署路径（头部大缺口）

现有库（2025-11-03 起，5211 文件）首次运行增量、请求更早 start：

- 缺失交易日数 > 20 → 自动走**按股票分片多线程路径**（复用初始化），逐股票补头部缺口 [start, 本地最早日)，调用次数 = 股票数 × 分片数（≤6000 行/片）。
- 完成标记同步建立后，后续例行增量回按日路径（1-2 次调用/日）。

## 测试策略

每个切片先写测试再实现：

- 最新性判断：16:00 cutoff 边界（16:00 前后、非交易日、周末、**非东八区主机**）、start 感知（显式更早 start 不短路）、已最新跳过、force 忽略。
- 缺失交易日计算：基于 sync_done 与 trade_cal 去重、本地覆盖子集、空标记、部分写入日**会重试**。
- 权威日历：trade_cal 成功缓存、失败用上次缓存、两者皆无 → job 失败。
- 初始化并发：分片（>6000 行拆多片）、令牌桶限速（不超 450/分）、共享 pro、每股票原子写、取消响应。
- 增量按日：缺失日逐个拉取、老股票合并去重（新覆盖旧）、新股创建、停牌日空返回跳过、**每日迭代前取消（已完成日保留）**、部分写入日不记完成。
- 新股合并 + stock_meta：新股文件创建且 stock_meta 含新股行；**stock_meta 刷新原子写**；**同步开始时无条件刷新（失败/早退也刷新）**。
- 并发互斥：提交第二个 market_sync → 409 / 拒绝；**并发双提交仅一个成功（TOCTOU）**；**重启恢复（残留 running 行被置 failed）**。
- 完成标记：按股票模式全部成功写 [start,end] 全部交易日、任一失败不写；**部分失败时失败股票持久化至 sync_retry_codes.json、下次按股票只拉失败子集、retry 全部成功后才写 sync_done**；**显式 codes 子集不写 sync_done**；**快速通道命中时缺失日校验（中段失败日仍重试）**；**存量库严格核对（交集）标记**；**reset 清 cache 后无残留标记（含 retry 文件）**。
- 日历与端钳制：未来 end 钳制为最近可交易日；**最近可交易日空返回不记 done、历史日空返回记 done + empty_count**。
- force：忽略 sync_done 重拉已标记日、成功日重写标记；**force 大区间（>20 日）仍走按日路径（不参与阈值分派）**。
- 契约：`submit_market_sync` 无 start_date 时默认 = 本地最早日期（日志提示实际值）；presenters `market_data_sync` 分支透传 force/codes。
- 真实场景回归：现有 2025-11-03 起数据，请求更早 start → 头部缺口被补拉（当前缺陷回归）。

## 验收标准

- 空库初始化：分片 + 令牌桶限速（≤450 次/分）完成全量，产出完整 bars（老股不丢头部历史）。
- 例行增量：本地已最新（且 start 未早于本地）时**除 trade_cal 日历刷新与 stock_basic 股票列表刷新外 0 次调用**；有缺失日仅拉缺失日（1 日 ≈ 1 次调用），老股票去重合并、新股自动创建并进入 stock_meta。
- 更早历史回填：请求更早 start 能补拉头部缺口（修复当前缺陷）。
- 16:00 边界：北京时间 16:00 前当日数据不可用，16:00 后可用；非东八区主机行为一致。
- 并发互斥：运行中提交第二个 market_sync 被拒绝；并发双提交仅一个成功；进程重启后残留 running 行被置 failed，不再永久 409。
- 部分写入/失败日：下次增量自动重试，不因日历并集而永久跳过；**中段失败日不因本地 max_date 达标而被快速通道跳过**。
- 按股票模式（初始化/升级补缺）完成后建立 sync_done 标记，后续例行增量回按日路径（1-2 次调用/日），不永久反复走按股票路径。
- `init-v2 --reset-runtime` 清空 `storage/cache/`（含 sync_done），无残留完成标记。
- 同步期间 selection/backtest 读取无半成品（bars/calendar/stock_meta/sync_done 全部原子写）；取消/进度/日志符合现有 job 约定。
- 全量测试通过，无回归；存量库（无 sync_done、无新列）平滑升级。

## 非目标

- 不做定时/自动调度。
- 不做复权（adj_factor 仍固定 1.0）。
- 不引入 Redis/Celery/PostgreSQL。
- 不改动 selection/backtest 的行情读取接口（`LocalParquetMarketStore` 契约不变）。
- 不改动 `market_sync_runs` 表结构（新增统计走 `jobs.result_json`）。
