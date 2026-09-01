# 行情同步机制重做（两阶段 + 原子账本）设计文档

**日期**: 2026-08-27
**状态**: 已确认（brainstorming 逐节批准，取代 `2026-08-27-market-data-foundation-design.md`）
**评审修订（2026-09-01）**: 按外部评审意见修正六项——① 断言①在全量批的语义统一为单日语义（消除与"全量任意断言失败丢弃"的矛盾）；② doubtful 日明确"写盘不入账 + 自愈/确认入账双路径"（消除增量死循环）；③ 北交所 `.BJ` 为 Tushare `daily` 物理不可得，永久剔除出拉取清单与 `expected(d)` 分母（实测 338 只缺口归因，`_BOARD_PREFIXES["bj"]` 未覆盖 920 段）；④ `expected(d)` 与拉取侧同一 `exclude_boards` 过滤；⑤ 换名→账本事务的失败窗口语义；⑥ stage-1/2 执行方式与 `execution_key` 关联、自动补齐不翻转主作业终态。
**二轮评审修订（2026-09-01）**: ⑦ 全量换名单向化（`bars → bars_prev`、`staging → bars`、重建空 staging），消除"双向互换 × 续传剔除"矛盾——后者会让第二次全量重建空转或丢数据（R20 覆盖）；⑧ §3.6 流程框织入带缺口提交约束（`sync_skipped` 非空须 `accept_partial_baseline`）；⑨ 通道二"立即补齐"定义 `job_type=market_backfill_codes` 并纳入互斥集；⑩ 全量自检明写"扫描 staging 全库统计逐日行数"。
**三轮评审修订（2026-09-01）**: ⑪ 全文耗时口径统一为 41 分钟 / 约 11,100 次（§3.9 表格与 §3.4 说明段同步）；⑫ 全量路径断言读回对象钉死：①②③ 对换名前的 `staging`、④ 对换名后的新 `bars`（落账前）；⑬ 换名优先 `renameat2(RENAME_EXCHANGE)` 原子交换消除 `bars` 缺失窗口，回退路径的窗口由 `bars_prev` 兜底（§9 增补恢复条目）。另：R11 补补齐作业互斥断言、§3.5 拉取侧补 `exclude_boards` 过滤表述、§6 面板④补互斥拒绝提示。
**四轮评审修订（2026-09-01）**: ⑭ 统一全量批自检失败的 staging 处置（消除 §3.6 与 §3.8 矛盾）：②③ 失败 = 数据可疑 → **丢弃 staging 从头重拉**（续传会复用坏文件）；取消 / env / 带缺口未确认 = 文件完整 → 保留续传；全量批 ④ 失败（换名后）**不置 `ledger_suspect`**（置则会逼迫重建已换名成功的数据）。成功条件同步修正为"断言②③ 通过"（① 为单日语义不中断批）。新增 R21、R20 措辞修正。
**五轮评审修订（2026-09-01）**: ⑮ R10 措辞收窄为"取消 / env 失败（非自检失败）"，与 R21 的 ②③ 丢弃语义显式对齐。至此遗留矛盾清零，评审批准进入实施计划阶段。
**范围**: `trendradar/domain/market/sync/*`（新建）、`trendradar/infrastructure/tushare/{syncer,calendar,markers,stocklist,rate_limit}.py`、`trendradar/infrastructure/storage/{schema,connection}.py` + 新增 store、`trendradar/app/services/{market_service.py,market_sync/*}`、`trendradar/app/jobs/executor.py`、`trendradar/interfaces/api/{presenters.py,routes/market.py,schemas/market.py}`、`frontend/src/pages/MarketData/*`、相关测试

---

## 1. 背景与问题

现状 `syncer.py`（696 行）把**决策规则**与**抓取动作**混在同一层，账本写入点散落在 3 个函数里。代码复核确认三类结构性缺陷，均无法靠打补丁同时消除：

| 编号 | 缺陷 | 取证 |
|---|---|---|
| P1 | **请求参数劫持决策**：UI 表单默认 `start=2019-01-01`（`MarketDataPage.tsx:64-68`），每次点击都判定为大区间 → 触发全市场 ~5,200 只 × 6 年重拉 | `decide_mode` L22-32 |
| P2 | **日历污染 → 假绿**：`plan_sync` 按**请求**的 `end` 拉日历并整文件覆写 parquet（L85-86），发生在 `uptodate` 判定（L124）**之前**；下次拉取失败时降级读旧账，`latest_tradeable_day` 被假日历截断 → 面板全绿而 08-24/25/26 永久静默缺失 | `plan_sync` L79-134、`calendar.save_trade_calendar` 为 replace 语义 |
| P3 | **打勾资格跟代码路径走而非覆盖率走**：新上市批越权打勾（L610-612）、欠账队列顶替全市场清单（L455-456）、空响应吞日（L633-638）、取消半路 `break` 留悬空（L488） | `sync_by_stock` / `sync_market` |

另有一项与本次重做同源的数据缺陷：**股票清单只拉在市股（`stock_basic list_status="L"`，实测 5,550 行），漏掉退市股（`"D"`，实测 339 行）**。退市股历史可正常拉取（实测 `000004.SZ` 返回 2,561 行，最晚 2026-07-13），缺失导致回测幸存者偏差，并使"全市场当日已捕获"这句话在历史上任一天都不成立。

## 2. 需求

- **N1 关注点分离**：同步**策略**（拉什么、怎么拉）与同步**状态**（成没成）互不派生，不互相传染。
- **N2 两阶段**：stage-1 = 交易日历同步，stage-2 = 行情同步；两阶段各自有独立状态记录。
- **N3 日历不被业务请求污染**：行情请求的 `start/end` 与日历写入范围彻底无关。
- **N4 原子提交**：一次同步要么整体入账、要么整体不动；日账本不存在"半截完成"。唯一例外是断言①（单日行数可疑），其原子粒度天然为"单日"，详见 §3.8 失败处置分类。
- **N5 不因环境故障误杀个股**：限流/额度/网络类失败永不累计成"某只股票拉不到"的结论。
- **N6 状态可见**：面板能回答"日历断供了还是行情断了、落后几天、缺几只股票"。
- **N7 无历史包袱**：个人单用户系统，**不做存量数据迁移**；本轮完成后用现成命令全清重建。

## 3. 设计

### 3.1 分层与模块布局（换大脑不换手）

保留正在稳定产数据的抓取/落盘原语，重写全部决策与记账逻辑：

```
trendradar/domain/market/sync/
    __init__.py
    planner.py      # build_plan(...)  纯函数，零 I/O —— 唯一决策出口
    selfcheck.py    # 4 条自检断言     纯函数（输入内存副本 + 集合）
    spec.py         # SyncPlan / PlanKind / FailureKind / 常量（BASELINE_START=2015-01-01 等）
trendradar/infrastructure/
    storage/sync_store.py     # 账本四表读写 + 单事务提交（唯一有权写账本的模块）
    tushare/fetch.py          # 自 syncer.py 原样迁移：_fetch_with_retry / _response_to_df /
                              # _attach_adj_factor / _align_columns / shard_ranges /
                              # _to_ts_code / exclude 过滤 / merge_day_bars / _fetch_daily_by_date
                              # 唯一改动：返回值携带 FailureKind（见 3.7）
    tushare/writer.py         # _atomic_write_parquet / flush_by_code / staging 单向换名（§3.6）
    tushare/runner.py         # run_incremental(days) / run_full(codes) / run_backfill(codes)
                              # 只执行与统计，不做任何"拉哪些天/哪种模式"的判断
    tushare/stocklist.py      # 改为 L ∪ D 两次调用，新增 delist_date 列
    tushare/calendar.py       # 仅保留 fetch_trade_calendar(pro, start, end)；save/load 删除
trendradar/app/services/market_sync/
    service.py                # stage-1 / stage-2 worker 编排、progress/cancel、状态落库
    commit.py                 # "临时副本 → 写盘 → 读回校验 → 单事务落账"
trendradar/app/services/market_service.py   # 保留 submit_* 门面，改调新模块
```

依赖方向仍为 `domain → infrastructure → app → interfaces` 单向。旧 `syncer.py` 在新模块接管全部调用方后删除；共存期间不向其新增任何逻辑（交付阶段划分与实施顺序由 `writing-plans` 阶段的实施计划给出，不在本设计文档范围内）。

**保留不动**：`LocalParquetMarketStore`（实测日历）、`data_store.py`、`rate_limit.py`（270/分 + burst 270）、`TokenBucket.acquire(timeout=60, cancel_check)` 语义。

### 3.2 不变式

- **INV-1 状态 = 事实**：任何被记为 `success` 的作业，其声称覆盖的每一天都在日账本里；任何在日账本里的日期都能通过读回校验（断言④）。宁可不写状态，不可写错状态。
- **INV-2 日历只增不减**：日历表只能被 `INSERT OR IGNORE` 扩大，任何业务请求参数都不得进入其写入路径（主键约束物理保证）。
- **INV-3 谁做的活谁记账**：只有"全市场清单 + 按日/全量批"能推进日账本；指定代码的补齐批（`BACKFILL_CODES`）**永不**触碰日账本。
- **INV-4 原子性适用于账本，不适用于文件**：文件层是按日期 upsert 的幂等写入（重复拉取无害，新行覆盖同日期旧行），因此自检失败时**不回滚已写文件**，只**不推进日账本**。
- **INV-5 环境故障不产生结论**：`env` 类失败不参与任何"个股永久不可得"的判定。

### 3.3 stage-1：交易日历

**存储**（进 `schema.py`，随 `init_schema` 幂等建表，复用 `StorageConnection` → `storage/app.db`，WAL + `timeout=30`）：

```sql
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date TEXT PRIMARY KEY              -- 'YYYY-MM-DD'，ISO，可直接与 polars Date 比较
);
```

**写入**：`INSERT OR IGNORE` + `executemany`（实测 336 行 × 3 次连刷含 WAL commit 共 16.9 ms；`SELECT MAX(trade_date)` 0.42 ms）。

**刷新规则**（无定时器、无手动按钮；每次 stage-1 运行只做一件事）：

| 情形 | 动作 |
|---|---|
| 表为空（首次） | `fetch_trade_calendar(pro, 2015-01-01, 今年 12-31)` → 实测 4,383 行，1 次调用 |
| 表非空 | `fetch_trade_calendar(pro, 今年 01-01, 今年 12-31)` → 实测 365 行，1 次调用 |
| 跨年 | 无需特殊逻辑：1 月 1 日那次刷的就是新年份（Tushare 已实测提前整年发布：2026 全年 242 交易日、2027 全年 244 交易日均可拉） |

单次 API 调用成本对比：现状每次同步从 1990 年起按年循环拉取 = **37 次调用**（UI 带 `start=2019` 时 8 次）；新方案 **1 次**。

**为什么每天都刷（不可省）**：§3.8 的原子提交使"日历幽灵日"（官方日历说是交易日、接口实际返回 0 行，如临时休市）的代价从"多花 2 次调用"升级为"**整个 stage-2 永久 failed**"。实际刷新频率为**每次 stage-2 触发刷一次**（1 次调用，连续点击即连刷，成本可忽略），天然保证每天至少 1 次，把这类日子的存活时间压到 1 天以内，是原子性的必要保险。

**消费方与降级**（stage-1 状态本身即面板第 ① 行）：

| 情形 | 判定 | 后果 |
|---|---|---|
| `MAX(trade_date) >= 北京今日` | 日历正常 | stage-2 继续 |
| `MAX(trade_date) < 北京今日` 且表非空 | stage-1 `failed`（拉取失败） | stage-2 **BLOCKED**，明确失败，面板灰态 |
| 表为空且拉取失败 | stage-1 `failed` | stage-2 `failed`（"日历未就绪"） |

**禁止**：任何"降级读旧日历后继续判断已最新"的路径（P2 的根源）。

### 3.4 stage-2 决策：`build_plan` 纯函数

```python
BASELINE_START = date(2015, 1, 1)      # 行情与日历共同下界（domain/market/sync/spec.py）
DATA_CUTOFF_HOUR = 16                  # 北京 16:00 前，今日尚不可得（沿用 latest_tradeable_day 语义）

class PlanKind(str, Enum):
    BLOCKED = "blocked"; REBUILD_REQUIRED = "rebuild_required"
    FULL = "full"; INCREMENTAL = "incremental"
    UPTODATE = "uptodate"; BACKFILL_CODES = "backfill_codes"

@dataclass(frozen=True)
class SyncPlan:
    kind: PlanKind
    latest_tradeable: date | None      # 官方日历 + 16:00 规则
    missing_days: list[date]           # 增量待拉集合，升序，含中段洞
    stale_days: int                    # 尾部连续覆盖缺口（面板主数字）
    reason: str                        # 进日志与 result_json，人类可读
```

输入全部由调用方（`service.py`）读好后传入：`today_cn`、`now_cn`、`calendar_days: set[date]`、`done_days: set[date]`、`ledger_suspect: bool`、`request: dict`。函数内零 I/O、零 `now()`。

**决策矩阵（自上而下，首个匹配生效）**：

| # | 条件 | kind | reason |
|---|---|---|---|
| 1 | `calendar_days` 为空 或 `max(calendar_days) < today_cn` | `BLOCKED` | "官方日历未就绪/过期，拒绝判断新鲜度" |
| 2 | `request["codes"]` 非空 | `BACKFILL_CODES` | "指定代码补齐（不参与日账本）" |
| 3 | `request["force"]` 为真 | `FULL` | "用户显式要求全量重建" |
| 4 | `done_days` 为空 | `FULL` | "首次建库" |
| 5 | `ledger_suspect` 为真 | `REBUILD_REQUIRED` | "账本曾被自检判为不可信，需人工确认重建" |
| 6 | `missing_days` 为空 | `UPTODATE` | "已覆盖至最近可交易日" |
| 7 | 其余 | `INCREMENTAL` | "待补 N 个交易日" |

**为何 `ledger_suspect` 不直接映射到 `FULL`**（列在第 5 行、即在 `force` 之后）：若自动转全量，则一次自检失败会让用户的下一次普通点击静默变成约 41 分钟 / 约 11,100 次调用的重建，可能直接烧掉当日额度。`REBUILD_REQUIRED` 的含义是“**拒绝执行任何拉取**，面板 ⑤ 明告原因并要求用户显式点全量重建”（届时 `force` 为真即命中第 3 行走 `FULL`）。`sync_meta.ledger_suspect` 仅在**一次全量成功（含带缺口显式提交）**后清除。

**两个口径明确定义**（`missing_days` 与 `stale_days` 不同，缺一不可）：

- `missing_days = {官方交易日 d | BASELINE_START ≤ d ≤ latest_tradeable} − done_days` —— 增量**实际拉取集合**，包含中段洞；只做尾部会令中段洞永不回填。
- `stale_days` = 从 `latest_tradeable` 沿官方日历反向回溯，第一个已入账日期为止的交易日数 —— **尾部连续水位**，是面板展示的主数字，也是用户直觉上的"落后几天"。当 `len(missing_days) > stale_days` 时，面板副行显示"总缺口 N 天"。

**关键变更：模式判定中不再存在任何天数阈值。** 现状 `>20 交易日 → full` 的启发式方向是反的，成本实测：

| 路径 | 请求量 | 耗时地板（270 次/分） |
|---|---|---|
| 增量（按日） | 2 次/天（`daily` + `adj_factor`）→ 400 天 = 800 次 | **3 分钟** |
| 全量（按股票） | 2 次/只 × 有效清单约 5,551（5,889 − 北交所 338，§3.6）≈ 11,102 次 | **约 41 分钟** |

即缺口越大越该走按日；全量只在"本地不可信"时进行（矩阵 3/4/5），与天数无关。

### 3.5 增量路径（日常主路径）

**串行执行，无并发**（1 天 0.45 秒、40 天 18 秒、400 天 3 分钟，并发只增加失败面）：

```
① stage-2 开头刷新股票清单：stock_basic(L) + stock_basic(D)，2 次调用
   → 写 stock_meta.parquet（含 list_date / delist_date，原子替换，沿用现机制）
② for d in plan.missing_days（升序，串行）:
      df = fetch_day_by_date(d)          # daily + adj_factor，各重试 3 次；
                                         # 响应经 exclude_boards 过滤（与 §3.8 分母同一过滤）
      断言① 未通过 → 该日数据**照常累积进 all_days（写盘）**，仅记入
                     `doubtful_days`、不进入声称集合，继续下一天（见 §3.8）
      其他异常（env/code/网络）→ 立即中止本批：不拉后续天、已攒内存数据丢弃、
                                不写盘、不落账（下轮重拉，代价 2 次调用/天）
      通过 → 累积到内存副本 all_days
③ 自检（内存副本，见 §3.8）断言③ → 不通过则整批不落账并置 ledger_suspect
④ flush_by_code：all_days 按 code 分组 → 每个受影响文件"读一次 + 按日期 upsert + 原子写一次"
   （含 doubtful 日——真实交易数据不因行数断言被丢弃，INV-4 幂等兜底；
   文件读/写次数 = 受影响代码数，与天数无关；08-24 上次失败留下的脏行被本次同日期新行覆盖）
⑤ 读回校验：scan 全库实测日历（5,211 文件实测 1.3 秒）→ 断言②④
   （声称集合已剔除 doubtful_days，不拖累整批）
⑥ commit.py：单事务写 sync_done_days（仅声称日，不含 doubtful）
   + 更新 sync_meta（含 `doubtful_days` 列表持久化、`ledger_suspect` 等）
```

**doubtful 日闭环（防"恒 failed"）**：doubtful 日仍留在 `missing_days`，每轮增量照常重拉（成本 2 次调用/天，可忽略）：

- **自愈**：重拉比值回升（限流造成的瞬态半截响应）→ 该日入账并从 `doubtful_days` 清除；
- **确认入账**：比值持续偏低即真实停牌（历史数据固定，重拉永不回升）→ 由面板 ⑤"确认入账"人工了结（§3.8 末）。

**新增上市股票无需任何特殊处理**：按日全市场响应天然包含当日已上市的股票，因此现有 `sync_market` 中"新上市批"整段（L604-616）删除。

### 3.6 全量路径（首次建库 / 自检判不可信 / 显式重建）

**待拉清单**（纯集合运算，1~2 次 API 调用取全集，不猜不扫盘）：

```
全市场清单 = stock_basic(L) ∪ stock_basic(D)                        # 实测 5,550 + 339 = 5,889
  ① 剔除 list_date > latest_tradeable
  ② 剔除北交所（`ts_code` 以 `.BJ` 结尾——Tushare `daily` 接口物理不提供其行情，
     永久剔除而非用户选择；实测本库 5,549 清单中 338 只无 bar 文件**全部**为
     北交所 `920xxx`，即 §3.8 实测 0.938 比值的全部缺口来源。注意不得沿用
     `_BOARD_PREFIXES["bj"]=("4","8")` 数字前缀识别：未覆盖 2023 后 920 段）
  ③ 剔除 exclude_boards（BSE/科创，默认全不勾，行为不变）
  ④ 每股区间钳制：start = max(BASELINE_START, list_date)
                    end   = min(latest_tradeable, delist_date or end)
     —— 钳掉退市后区间，消除"拉回空 DataFrame 算成功还是失败"的歧义（现状 `sync_by_stock.fetch_one` 中 `if data.is_empty(): continue`，L470 一律当成功）
  ⑤ 剔除 staging 目录中已存在的文件（续传）
  ⑥ 剔除 sync_skipped 名单
  = 本轮待拉
```

**执行与提交**：

```
ThreadPoolExecutor(max_workers=6) + TokenBucket(270/min)   # 沿用现状实测参数
逐只写 staging/bars/（本地 bars/ 一字不动）
→ 自检（扫描 staging 全库：断言① 逐日行数、断言② 读回日历 ⊇ 声称集、
  断言③ 文件结构——**①②③ 全部对 staging 做**，换名前 bars 仍是旧数据，见 §3.8）
→ 断言②③ 通过（断言① 为单日语义、不中断批，未过日记入 `doubtful_days`；
  且 sync_skipped 为空，或用户显式传
  accept_partial_baseline=true——带缺口提交约束，见 §3.7）：
        单向换名：bars → bars_prev（保留上一版，已有则覆盖，只留最近一版）、
                  staging → bars，随后重建空的 staging
        + 落账前对新 bars 校验断言④（换名后、事务前，守 INV-1；
          若失败：不落账、作业 failed、**不置 ledger_suspect**，文件已新账本仍旧，
          下轮增量幂等自愈——与下方换名窗口同一语义）
        + 单事务用（本次覆盖区间 − doubtful_days）替换 sync_done_days
        （断言① 未通过的日子用单日语义：数据随换名入库，仅该日不入账、
         记入 `doubtful_days`——见 §3.8）
→ 断言②③ 不通过：**丢弃 staging**（数据可疑，续传会复用坏文件），
        作业 failed，本地数据不变（下轮全量从头重拉）
→ 取消 / env 失败 / 带缺口未获确认：
        staging 原地保留（文件完整、仅缺股票文件，下次续传），作业 failed，本地数据不变
```

真删本地再重拉被否决：约 11,100 次调用在限流下最短 41 分钟，中途失败会留下"回测选股全废且无法回退"的状态；换名方案 100% 保留"要么全新要么原样"的语义（本地数据实测 81 MB / 5,211 文件 / 单文件 16 KB，暂存代价可忽略），且失败自动回退。

**单向换名，不做双向互换（二轮评审修正）**：成功换名后立刻重建空 `staging`，因此待拉清单 ⑤"剔除 staging 中已存在文件（续传）"只可能命中**本次中断残留**，不可能命中上一版数据。若做成 `bars ↔ staging` 双向互换，成功一轮后 staging 里躺着完整上一版，第二次全量重建会把全清单"续传"剔除干净 → 空转或把 `bars` 换成空目录，击穿"要么全新要么原样"。上一版由 `bars_prev` 承担（只留最近一版，供回滚参考，即 R10 断言对象）。**实现上优先用 `renameat2(RENAME_EXCHANGE)` 原子交换 `bars` ↔ `staging`**（Linux x86_64 部署），再把落到 staging 位的旧版移到 `bars_prev`、重建空 staging——全程 `bars` 路径无缺失窗口；平台不支持时回退两步 rename，接受毫秒级窗口（恰在窗口内崩溃则 `bars` 短暂缺失，从 `bars_prev` 手动恢复，§9）。

**换名与账本事务的原子性窗口**：目录换名与 SQLite 单事务无法原子绑定，固定顺序"**换名在先、事务在后**"。事务失败 → 作业 `failed`，此时文件已是新数据而账本仍旧；下一轮增量的 `missing_days` 天然包含这些日子，幂等 upsert 重拉后入账即自愈，**无需专门恢复逻辑**。反序（事务先、换名后）会令账本超前于文件、直接违反 INV-1，否决。

### 3.7 失败分类与逃逸阀（N5）

**前置改造**：`_fetch_with_retry` 现在把所有失败压成 `None`，丢失了分类信息。改为返回带类别的结果：

```python
class FailureKind(str, Enum):
    ENV = "env"; CODE = "code"; UNKNOWN = "unknown"; OK_EMPTY = "ok_empty"
```

现有代码已具备分类依据（`syncer.py:17-18`）：`RATE_LIMIT_MSG = "频率超限"`、`IP_BAN_ERROR_MSG = "每分钟最多访问该接口"`。

| 类别 | 判据 | 计入该股失败次数 |
|---|---|---|
| `env` | 频率超限 / 每分钟上限 / bucket 取令牌超时 / 网络不可达 / HTTP 5xx / token 当日额度耗尽 | **否，一次都不计**（INV-5） |
| `code` | 接口明确拒绝该参数（"参数错误"/"无此股票"）、返回结构无法解析、该股 `adj_factor` 异常 | 是 |
| `unknown` | 其它异常 | 是，`last_error` 原文入库供人工判定 |
| `ok_empty` | 经 ④ 钳制后理论不该出现的空区间 | 否，视为成功 |

**整批熔断（主保险，优先于分类）**：单轮失败率 **> 5%** ⇒ 判定环境故障 ⇒ 本轮全部失败**一律不计次**，作业 `failed` 并保留 staging。理由：限流与额度是同时打在随机一批股票上的，一次挂约 300 只不可能是这 300 只各自有病；即使分类判错也不会误杀。

**出列门槛**：仅在"健康轮"（失败率 ≤ 5%）中，同一 code 连续 **3 次全量作业**失败才进 `sync_skipped`；中间任一次成功即计数归零。

```sql
CREATE TABLE IF NOT EXISTS sync_skipped (
    code         TEXT PRIMARY KEY,
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT,
    first_seen   TEXT NOT NULL,
    last_attempt TEXT NOT NULL
);
```

**出列不是终局（两条复活通道）**：

- **通道一（自动）**：每次 stage-2 运行结尾，只要 `sync_skipped` 非空（**含 `UPTODATE` 与 `INCREMENTAL` 两种批**，否则无缺口时名单永无重试机会）即自动带一轮 `BACKFILL_CODES` 补齐，成本 N × 2 次调用（N 通常为 0），成功即销账。**补齐失败不翻转主作业终态**——主作业按日账本判定照实记 `success`/`failed`，补齐的成败只写入 `coverage` 与日志，避免"已最新"的点击被后台个股拉取失败染红；
- **通道二（手动）**：面板"立即补齐"按钮触发同一批，并先清空 `attempts` 计数（用于误判后重新给 3 次机会）。此为**独立提交的作业**，`job_type=market_backfill_codes`，纳入 executor 互斥集（§3.9），不得与主同步并行写文件；

**带缺口提交需显式确认**：`sync_skipped` 非空时，全量批不得自动推进日账本；仅当名单内全部为 `code`/`unknown` 类（无 `env` 残留）且用户显式传 `accept_partial_baseline=true` 时才提交并保留面板红字。若缺口含 `env` 类，拒绝提交、等下轮——限流造成的缺失自己就会好。

### 3.8 自检（在临时副本上，原子）

| # | 断言 | 时机 | 抓的是哪类鬼 |
|---|---|---|---|
| ① | 每个声称完成的交易日：`行数 ≥ 0.75 × expected(d)`，其中 `expected(d) = |{s ∈ 有效清单 : s.list_date ≤ d 且 (s.delist_date 为空 或 s.delist_date ≥ d)}|`；**有效清单 = L∪D − 北交所 − exclude_boards，与拉取侧同一过滤**（§3.6，避免分母与配置耦合导致比值系统性偏低） | 内存副本 | 接口返回空表 / 只回半截（限流受害者） |
| ② | 读回全库实测日历 ⊇ 本批声称日集合 | 写盘后 | 写了但没落盘 / 落错文件 |
| ③ | 每个被改写的文件：日期单调递增、无重复日期、`open/high/low/close` 无 NaN | 写盘前 | 拼接顺序错 / upsert 漏列 |
| ④ | `sync_done_days ⊆ 实测日历`（本批新增部分） | 落账前 | 状态与实际漂移 |

**阈值 0.75 的实测依据**（L ∪ D 精算 `expected`，实际行数为接口真值）：

| 日期 | `expected(d)` | 实际行数 | 比值 |
|---|---|---|---|
| 2015-06-01 | 2,733 | 2,335 | **0.854** |
| 2018-06-01 | 3,521 | 3,333 | 0.947 |
| 2021-06-01 | 4,379 | 4,411 | 1.007 |
| 2024-06-03 | 5,364 | 5,341 | 0.996 |
| 2026-08-21 | 5,549 | 5,205 | 0.938 |

比值区间 0.854~1.007：2015 年偏低是当年大面积停牌（真实缺口，非数据损坏），故取 0.75 留 14% 缓冲；而"只回半截"的比值约 0.5，仍能稳定报警。比值可略超 1（`stock_basic` 快照滞后），因此只做下限断言。2026-08-21 的 0.938 经核实缺口全部来自北交所 338 只（Tushare `daily` 不提供），按"有效清单"剔除后分母与接口真值对齐，比值回到 ≈1.0，**0.75 缓冲不被配置或接口边界持续消耗**。

**失败处置分三档，不是一句“全失败”**（差别在于“失败说明了什么”）：

| 断言 | 失败说明 | 处置 | 置 `ledger_suspect` |
|---|---|---|---|
| ① 单日行数比值不足 | 只有**这一天**的接口响应可疑（休市/半截） | 该日数据**照常写盘**（INV-4 幂等），**不入账**、记入 `doubtful_days`；其余日照常写盘与落账；作业 `failed`；面板红字 + "确认入账"入口（见下） | 否 |
| ③ 副本结构异常（重复日期 / NaN）【增量批】 | **合并逻辑本身**出错，可能污染多个文件 | 整批不落账 | 是 |
| ②④ 读回缺失【增量批】 | 账本与文件已漂移 | 整批不落账 | 是 |
| 全量批 ②③ 失败 | staging 数据可疑（读回缺日 / 结构损坏），**续传会复用坏文件** | **丢弃 staging**，下轮全量从头重拉，本地不变（§3.6） | 是 |
| 全量批 ④ 失败 | 换名后、落账前对新 bars 校验不过 | 不落账、作业 `failed`；文件已新账本仍旧，下轮增量幂等自愈（§3.6） | **否**（置 suspect 会逼迫重建已换名成功的数据） |
| 全量批 ① 失败 | 与增量批**同一单日语义** | 换名照常入库（数据真实），该日不入账（入账区间 = 覆盖区间 − `doubtful_days`），面板红字 + "确认入账" | 否 |

**续传适用范围限定**（四轮评审修正）：待拉清单 ⑤ 的"续传剔除"只对**文件完整**的中断场景有效（取消 / env 失败 / 带缺口未确认）；自检判数据可疑（②③）时必须丢弃 staging——续传按"文件已存在即跳过重拉"，损坏文件会被跳过并随下次换名进入 `bars`。

> **① 为何从“整批原子”中剖出来**（对 §2-N4 的唯一例外，已确认）：若“一天可疑 → 整批不落账”，则一个长期行数偏低的日子（历史上真实出现过单日 1,400 只停牌，比值低至 0.49）会让 stage-2 **每天都失败且永远无法自愈**。① 的原子粒度天然是“单日”（该日要么完整落账要么完全不留勾），不侵犯 INV-1；而 ②③④ 的失败是全局性的，保留整批不落账。

**"确认入账"（doubtful 日的人工逃逸阀）**：比值低本身无法区分"真实停牌"与"接口半截"，本设计**一律不自动入账**，交人工裁决：面板 ⑤ 红字行附"确认入账"按钮（仅 `doubtful_days` 非空时出现），对应一个**同步 API（非作业）**：先校验 `doubtful_days ⊆ 读回实测日历`（数据确在盘上，守住 INV-1），再把该日写入 `sync_done_days` 并从 `doubtful_days` 清除；校验不通过则拒绝并提示先重拉。与自愈路径分工：瞬态半截靠下轮重拉自愈（§3.5），真实停牌（历史数据固定、比值永不回升）靠确认入账。由此**首次全量建库命中 2015 停牌日不再是死循环**——数据已入库，用户确认后即完成，不会触发 REBUILD_REQUIRED 反复重建。

### 3.9 状态机

两个阶段各自一行记录，均落在现成 `jobs` 表（**不新增状态表**）：

| stage | `job_type` | 生命周期 |
|---|---|---|
| stage-1 日历 | `market_calendar_sync` | `queued → running → success/failed`（<1 秒） |
| stage-2 行情 | `market_bars_sync` | `queued → running → success/failed/cancelled`（增量 0.5 秒~3 分钟；全量约 41 分钟，可跨天） |

`running` 必须保留（面板需区分"排队中"与"正在跑"）；`queued` 沿用现名，不引入 `pending`。一次 UI 点击 = stage-1 一行 + stage-2 一行；**stage-1 是 stage-2 worker 内的同步步骤**（不经 executor 二次提交、非嵌套异步作业）：stage-2 起跑前先执行 stage-1 并等待其完成，再读日历表做硬检（§3.3），确保决策不基于旧日历。两行 job 共用同一 `execution_key` 关联（现成 `executions` 表），面板 ① 行"查看控制台"跳 stage-1 job、③ 行跳 stage-2 job。

需一并修掉的现状坑：

1. `executor.py:36-43` 的互斥判断硬编码 `job_type == "market_sync"` —— 改名后互斥静默失效，改为集合同判（`{"market_bars_sync", "market_backfill_codes"}`，后者为通道二"立即补齐"独立作业，§3.7）；同一 stage 的"查表 + 插入"需在同一事务内完成以消除 TOCTOU。
2. `executor.py` 在 worker 返回后把状态覆写为 `cancelled` 并抹掉 `error_message` —— 改为尊重 worker 已写入的终态（worker 若已写 `failed("Cancelled by user")` 则不覆写）。
3. stage-2 起跑前硬检日历（§3.3 表）：不满足即 `failed`，**不降级继续**。

## 4. 状态接口与面板数据源

`/api/market-data/status` 新增 5 组字段（替换旧 spec 的 5 平铺字段）：

```jsonc
{
  "calendar":    { "status": "success", "max_trade_date": "2026-12-31",
                   "covers_today": true, "job_id": "..." },
  "freshness":   { "trusted_through": "2026-08-21", "latest_tradeable": "2026-08-27",
                   "stale_days": 4, "total_missing_days": 4 },
  "bars_sync":   { "status": "failed", "finished_at": "...",
                   "error_message": "...", "job_id": "..." },
  "coverage":    { "missing_codes": 3,
                   "skipped": [{ "code": "...", "attempts": 3, "last_error": "..." }] },
  "consistency": { "ledger_suspect": false, "marker_mismatch": false,
                   "doubtful_days": ["2015-07-08"] }
}
```

- `doubtful_days` 为日期列表（`sync_meta` 持久化），面板 ⑤ 显示条数与"确认入账"入口；确认入账是同步 API 不是作业（§3.8）。
- 计算全部落在 `presenters._compute_market_status()`，随现成 30 s TTL 缓存；`coverage.skipped` 取前 10 条明细。
- `stale_days` 基于**读回实测**的文件日历（不信任 `done_days`），与 `marker_mismatch` 同源对账。
- `last_sync` 类字段改为 `bars_sync`：`SELECT ... FROM jobs WHERE job_type='market_bars_sync' ORDER BY id DESC LIMIT 1`，失败/取消照实返回。
- 缓存失效：`invalidate_market_status_cache()` 由 `service.py` 的 `try/finally` 在**任何终态**后调用。
- 三个数字同源：面板 `stale_days` = planner 的决策输入 = 自检的断言对象。

## 5. 清理（无迁移）

> 下表每一条均按 2026-08-27 对当前代码的**实测引用情况**标注状态（含行号）。实现时每项仍需先 `grep` 复核：上一轮清理提交只删了 `sync_kline` / `_is_up_to_date` / 路由与 schema 死字段，**未触及本表中标为“实测仍在”的条目**（该提交信息中提及 `market_sync_runs` 写入与前端幽灵字段属表述过宽，此处以代码为准）。

| 动作 | 细节 |
|---|---|
| 不迁移、不兼容读 | 代码**完全不认识** `sync_done.json` / `sync_retry_codes.json` / `trade_calendar.parquet`；无搬迁、无 `.migrated` 留档 |
| 重建方式 | 现成命令 `python -m trendradar.cli init-v2 --reset-runtime --confirm-reset`（实测已删除 `app.db` + `-wal/-shm`、`objects/`、`market/`、`cache/`），随后 UI 点"全量重建" |
| `markers.py` | `load/save_sync_done`、`load/save_retry_codes` 全删；文件若无剩余调用方则整体删除 |
| `calendar.py` | `save_trade_calendar` / `load_trade_calendar` 删除，仅留 `fetch_trade_calendar` |
| `syncer.py` | 新模块接管调用方后整体删除；`decide_mode` / `plan_sync` / `is_up_to_date` / `missing_trade_days` / `latest_tradeable_day` 分别并入 `planner.py`（前四者）与 `spec.py`（后者，纯函数保留） |
| `market_service._register_market_sync_metadata` | **实测仍在写入**（`market_service.py:59` 调用、`:124` INSERT；`tests/app/test_execution_registration.py:231` 仍断言该表行）：函数与调用点一并删除，其中的 `register_execution` 保留并更名 `register_market_sync_execution` |
| `market_service.get_market_status` | **实测仍在**（`market_service.py:141`）：已复核全仓零调用方，且是 `market_sync_runs` 的最后一个读取方（`:157`），随本项删除 |
| `schema.py`（DDL 删除） | **顺序要求**：先删上述写入方/读取方及其测试断言，再删 `market_sync_runs` DDL 与索引（`schema.py:95/111` 实测仍在）—— 颠倒会使同步作业运行期直接崩；老库残留表不 DROP，孤儿无害 |
| `schema.py`（新表） | 新增四表 DDL：`trade_calendar`（§3.3）、`sync_done_days` / `sync_meta`（§5 末）、`sync_skipped`（§3.7） |
| `stocklist.py` | 清单 L → L ∪ D（2 次调用），新增 `delist_date` 字段；北交所按 `ts_code` 的 `.BJ` 后缀识别并剔除（§3.6），**废弃** `_BOARD_PREFIXES` 数字前缀方案（`("4","8")` 未覆盖 920 段，实测 338 只北交所全为新号段） |
| 前端类型 | `TradingDatesResponse` 删除后端从不返回的 `effective_to` / `latest_data_date`（**实测仍在** `frontend/src/types/marketData.ts:4-5`，属待办） |

新增账本表全量 DDL（`sync_meta` 存 `ledger_suspect`、`last_full_success_at`、`doubtful_days`（JSON 日期列表）三个键）：

```sql
CREATE TABLE IF NOT EXISTS sync_done_days (
    trade_date TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

## 6. 前端：入口与面板

**入口（用户选定的 A 方案）**：

- 主按钮 **`补齐到最近可交易日`** —— 前端**不传任何日期**，由 `build_plan` 自行推算 `(trusted_through, latest_tradeable]`；
- 高级区（`Collapse`）：`全量重建`，说明文案固定为"约 41 分钟 / 约 11,100 次调用 / 将覆盖现有数据"，二次确认弹窗；`exclude_boards` 移入此区（默认全不勾）；`accept_partial_baseline`（§3.7）仅当 `coverage.missing_codes > 0` 时作为二次确认弹窗内的一个必选勾项出现；
- **删除** start/end 表单（连同 `dayjs("2019-01-01")` 默认值的**两处**出现：`MarketDataPage.tsx:65` 与 `:121`，即 P1 病根）；
- 日历不提供手动刷新按钮（随每次同步触发刷新，1 次调用，无需单独入口）；
- **执行入口契约变更**（四个实测改动点）：`MarketDataPage.tsx:45-46` 的 `submitExecution({ type: "market_data_sync", ... })` 改为 `market_bars_sync`，`params` 只剩 `force` / `exclude_boards` / `accept_partial_baseline`（**不再有 `start` / `end` / `codes`**）；`types/execution.ts:34` 联合类型同步；`presenters.py:711-722` 的 `jtype` 分支及其 `job_type = "market_sync"` 改名；执行控制台的 job_type 标签映射。旧 `market_data_sync` 分支直接删除（全仓无其他调用方，本系统无外部 API 用户）；`codes` 仅由 CLI 高级回填命令传入，UI 不暴露。

**面板**（沿用已选定的 C 布局：顶部 `Row`，左 `Col xs=24 lg=14` 放现有 4 指标卡 2×2，右 `Col xs=24 lg=10` 为"数据地基"卡），右卡行序 5 → 6：

```
① 日历              覆盖至 2026-12-31 · 正常              ← calendar.status；failed 红 + 查看控制台
                                                        ← 表空且拉不到：灰"日历未就绪，行情同步已阻断"
② 数据新鲜度        落后 4 个交易日                       ← stale_days：0 绿 / >0 红 / null 灰"未建库"
                    最近可交易日 08-27 · 可信边界 08-21    ← latest_tradeable + trusted_through 小字
                    （total_missing_days > stale_days 时追加"总缺口 N 天"）
③ 最近一次行情同步   08-27 18:04 · 失败 · 查看控制台 →     ← bars_sync；null 时"暂无同步记录"
④ 个股覆盖          缺 3 只（连续失败）· 立即补齐          ← coverage；0 只时灰"无缺口"；
                                                        "立即补齐"被互斥拒绝（主同步运行中）时前端提示"同步进行中，稍后再试"
⑤ ⚠ 账本曾被判不可信 / N 个交易日行数异常已跳过（确认入账）  ← ledger_suspect 或 marker_mismatch 或 doubtful_days 非空才出现
                                                        ← doubtful 非空附"确认入账"按钮（§3.8）；suspect 时额外附"需手动全量重建"提示与按钮
⑥ 基线              2015-01-01 起 · 81 MB · 5,211 只      ← 本地存储卡（数字为当前库实测），沿用现状
```

控制台链接为 `<a href>`（同 SPA 路由）。

## 7. 测试计划（TDD，基线 378 passed）

| 编号 | 对应需求 | 断言 |
|---|---|---|
| R1 | N3 | 传入 `end=08-21` 的窄请求跑完整流程 → 断言 `trade_calendar` 表 `MAX` 不变、只增不减；连续两次 stage-1 后表内容为今年∪历史并集 |
| R2 | N3/INV-2 | stage-1 拉取失败 + 表内日历过期 → 断言 stage-2 返回 `PlanKind.BLOCKED`、作业 `failed`、日志无"已最新" |
| R3 | N2 | 表空 + 拉取失败 → stage-2 `failed("日历未就绪")`，而非静默跳过 |
| R4 | N1/§3.4 | `build_plan` 决策矩阵 7 行表驱动（BLOCKED / codes / force / 首建 / suspect→REBUILD_REQUIRED / 无缺口 / 增量），含 `stale_days` 与 `missing_days` 在中段洞场景下取值不同的用例；额外用例：`suspect=true` 且未传 force → **不得**返回 FULL |
| R5 | N4/INV-4 | 注入“第 37 天网络失败”→ 断言本批不写盘不落账；注入“文件未落盘”（②）与“账本超集”（④）→ 各断言整批零新增且 `ledger_suspect=true`；注入“某文件写失败”→ 断言作业 `failed` 且已写文件不回滚 |
| R6 | 断言① | 造 2015 场景 `2335/2733 = 0.854` → 通过；造 `0.5` → **该日数据写盘但不入账**、记入持久化 `doubtful_days`，同批其余日照常落账、作业 `failed`、`ledger_suspect` 保持 false（验证 §3.8 的例外不致永久堵死） |
| R7 | N5 | mock `频率超限` 打挂 300 只 → 断言 `sync_skipped` 为空、`failed(env)`；mock 同一 code 在 3 个健康轮中各失败 1 次 → 才进名单；中间插入 1 次成功 → 计数归零 |
| R8 | N5 | `sync_skipped` 含 `env` 残留时，`accept_partial_baseline=true` 仍被拒绝提交 |
| R9 | INV-3 | `BACKFILL_CODES` 批成功 → 断言 `sync_done_days` 无新增、skip 名单该项被销账 |
| R10 | §3.6 | 全量中途取消 / env 失败（**非自检失败**，自检失败见 R21）→ 断言 `bars/` 目录内容一字未改（换名前）、staging 保留续传；成功 → 断言换名后上一版存在于 `bars_prev`、staging 为空 |
| R11 | §3.9 | `presenters` 各态单测（stage-1 failed 阻断、`trusted_through=null`、`missing_codes>0`）+ `test_api_contract.py` 契约；executor 互斥覆盖 `market_bars_sync` 与 `market_backfill_codes`（两者互相排斥）与终态不覆写 |
| R12 | §5 | `test_schema.py` 断言四新表存在、`market_sync_runs` 已移除；`stocklist` 断言 L ∪ D 合并、`delist_date` 列与 `.BJ` 剔除 |
| R13 | §3.8 全量① | 全量批含一个"停牌日"（比值 0.49）→ 断言 staging 照常换名、该日文件有数据、不在 `sync_done_days`、`doubtful_days` 记 1、其余日入账、作业 `failed` 且 `ledger_suspect` 保持 false（**不得**丢弃 staging + suspect 死循环） |
| R14 | §3.5 自愈 | 第一轮增量某日比值 0.5 → 写盘不入账；第二轮同日出参回升至 0.9 → 该日入账、`doubtful_days` 清除、作业 `success` |
| R15 | §3.8 确认入账 | 确认入账 API：`doubtful_days ⊆ 读回日历` → 入账并清除；含不在盘日期 → 拒绝且不写账本 |
| R16 | §3.6 分母 | 勾选 `exclude_boards` → `expected(d)` 同步扣除、断言①不误触发；`.BJ` 股永不进入拉取清单与 `expected` |
| R17 | §3.6 换名窗口 | 注入换名成功后账本事务失败 → 断言作业 `failed`、文件为新数据、账本仍旧；下一轮增量幂等补齐入账 |
| R18 | §3.7 补齐终态 | `UPTODATE` + 自动补齐 env 失败 → 主作业 `success`、`coverage` 保留缺口与失败记录 |
| R19 | 幂等 | 增量重拉同一天 → 断言该日行被覆盖、历史行不变（承接 `_align_columns` 教训） |
| R20 | §3.6 单向换名 | 全量成功后再次 `force` 全量 → 断言待拉清单未被 `staging` 中上一版残留"续传"剔除（成功换名后 staging 为空，全部重拉）、`bars` 被新数据替换、上一版在 `bars_prev`（捕获双向互换导致的空转/丢数据） |
| R21 | §3.6/§3.8 staging 处置 | 注入全量批 ③ 失败（staging 文件损坏）→ 断言 **staging 被丢弃**、作业 `failed`、`ledger_suspect=true`；下一轮全量从头重拉、**不续传复用坏文件**；② 失败（读回缺日）同断言丢弃；取消 / env 失败 → 断言 staging 保留续传 |

旧同步测试约 53 个中约 35 个断言的是现有纠缠行为，随新规则重写；`test_sync_plan.py` 与 `test_sync_planner.py` 两个近乎同名的文件合并为一个。前端验收：`tsc` + `vite build` 通过；uvicorn 冒烟看板上六行真实数据（含手动制造一次取消、一次限流 failed）。

## 8. 明确不做（YAGNI）

- 存量数据迁移与向后兼容读（N7：个人系统，全清重建）。
- 逐股逐日覆盖对账（5,889 × 4,000 单元格级校验）；以日账本 + 断言①④ + 全量重建兜底。
- 常驻定时器 / cron。stage-1 由每次 stage-2 触发时顺带执行，进程内无 sleep。
- 欠账本 `sync_retry_codes.json` 的任何等价物；跨作业进度只由 staging 目录与 `sync_skipped` 承载。
- 停牌股语义：日账本含义是"该日全市场响应已捕获并落盘"，不承诺个股每日一行。
- `exclude_boards` 与日账本的交互：被排除板块照常计入"已齐"（用户主动不要，属产品选择）。
- 日历手动刷新按钮、增量并发度调参 UI、多 token / 多账号轮转。

## 9. 运维提醒

- **首次上线本轮改动**：`init-v2 --reset-runtime --confirm-reset` 清空 → 启动服务 → UI 点"全量重建"。首次建库前面板六行均为灰/空，属预期，不是故障。
- **全量可合法跨天完成**：若撞上 token 当日额度上限，作业 `failed(env)` 且 staging 原地保留；次日再点一次即从断点续拉（`env` 不计次，不会误杀）。全量清单（剔除北交所后）实测约 5,551 只 ≈ 11,100 次调用 ≈ 41 分钟地板。
- **限流余量**：TokenBucket 上限 270 次/分，实测该 token 档真实上限 300 次/分（留 10%）。若全量频繁撞 `env`，唯一该调的是这个数，不该动逃逸阀。
- **`bars_prev` 是唯一的回滚/崩溃恢复手段**：全量成功后上一版保留在 `bars_prev`（只留最近一版）；若恰在两步换名回退路径的窗口内崩溃导致 `bars` 缺失，手动 `mv bars_prev bars` 即恢复（§3.6）。
- **退市股**：并入 339 只退市股后回测样本含已退市标的；若某些策略假设"股票池恒为在市股票"，需要在看数时意识到这一点（这是修正幸存者偏差的预期结果）。
- **`stock_meta` 语义变化**：行数 5,549 → 约 5,889，新增 `delist_date` 列；选股/回测若有按行数校验的地方需复核。
