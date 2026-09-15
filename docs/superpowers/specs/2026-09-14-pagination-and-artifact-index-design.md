# 分页契约与产物索引设计（选股/回测明细、列表、任务可见性）

> 版本：v2（2026-09-14，含两轮自查整改）。范围：B/C/D 全部——选股详情分页（P1，已实施）、
> 回测报告分页与趋势服务端化（P2）、列表端点与 `jobs.result_json` 瘦身（P3/P4）、
> 运行中任务可见性（P5）、计数口径（P6，已随 P1 落地）、报告首屏（P7）、前端拆包（P8）、
> 零散项（P9）；第一批遗留的**报告预生成缓存与静态资源缓存头明确不做**（见 §2 末）。
> **实施顺序**：P1 ✅ → P3/P4 → P2 → P5 → P7/P8/P9；每个 P 各自出计划、TDD、独立提交。

## §1 背景与实测

2026-09-14 在真实数据上测得（本机，`:8000` 同代码同数据）：

| 端点/页面 | 现状 | 成因 |
|---|---|---|
| `GET /api/selection-results/{key}`（最坏样本 `20260821_175312_selection_ecd13a9e`） | **12.27s / 120.6MB**，727,378 条 picks，仅 `json.dumps` 5.5s | `include_detail=True` 全量直出；前端 15 条/页客户端分页 |
| `GET /api/backtest-results/{key}/report` | 15.3MB（gzip 2.1MB），首屏 2.2s；明细 3,044 页 | 全量 trades/skips/open_positions 直出 |
| `GET /api/selection-results?limit=50` / `backtest-results?limit=50` | 各 0.29s，随历史条数线性恶化 | 每个 run 整份解析 `signals.json`（最大 13.2MB）/`result.json`（最大 15.9MB） |
| `jobs.result_json` | 最大 **12.88MB**（与产物 `result.json` 同内容） | worker 把完整 output 传给 `ctx.succeed`；**全仓无消费方**（前端只用 console/cancel） |
| 产物索引现状 | 回测已有 `trades.parquet` 869KB / `skips.parquet` / `open_positions.parquet`；选股只有 `signals.json` | worker 落盘时写/不写 |

**可行性实测**（最坏样本 727,378 条 picks 落 parquet）：

| 操作 | 耗时 |
|---|---|
| 建帧 + 写 `picks.parquet` | 0.37s → **0.8MB**（JSON 13.2MB） |
| `total` 计数 | 16ms |
| 过滤 + 分页（strategy=…） | 6–53ms |
| 排序 + 分页（strategy,date,code） | 95ms |
| 去重股票数 | 27ms |

即：分页端点的成本从「O(产物字节) + O(总行数)」降到「O(页)」。

## §2 需求（用户已拍板）

| 编号 | 需求 | 备注 |
|---|---|---|
| P1 | 选股详情分页：后端服务端分页 + 前端**统一分页器** | 前端分页器要能复用到回测明细 |
| P2 | 回测报告分页（trades/skips/open-positions）+ 趋势序列服务端聚合 | 趋势图需要全量 trades，改为后端出聚合序列 |
| P3 | 列表端点不再解析大产物 | 走索引文件 |
| P4 | `jobs.result_json` 瘦身为摘要 | 无消费方；顺带缩 `app.db`（现 18.1MB） |
| P5 | 运行中任务可见性：`/api/executions/active` + 侧边栏徽标 + 控制台入 AppShell + SPA 内跳转 | |
| P6 | 计数口径修正 | 见 §9.1 |
| P7 | 报告首屏结构（趋势图上移、规则折叠） | |
| P8 | 前端拆包（路由 lazy + recharts 独立 chunk） | 主包现 1.71MB |
| P9 | 零散：meta 读取合并、响应去无用字段、`stock_count` 口径 | §9.2–9.4 |

**明确不做**：报告预生成/进程内缓存（②c）、静态资源 Cache-Control（③）——用户已确认放弃；
`_MARKET_STATUS_TTL` 保持 30s 不变。

## §3 分页契约（P1/P2 共用）

### 3.1 请求

```
GET <端点>?offset=<int≥0，默认 0>&limit=<1..200，默认 50>
           &sort_by=<白名单列，默认产物固有序>&sort_dir=asc|desc（默认 asc）
           &<过滤器…>
```

- 非法 `sort_by` → 400（白名单外一律拒绝，不做静默回落）；
- `offset`/`limit` 越界 → FastAPI `Query(ge=/le=)` → 422（与现有 `?limit=` 端点一致）；
- 过滤参数为端点特有：picks 用 `strategies` 与 `dates`（重复参数形式 `?strategies=A&strategies=B`）；
  trades/skips 用 `strategies`。
- **过滤参数 `strategies` 的取值语义（两套端点不同，必须写明）**：
  - picks 端点：**策略显示名**（选股产物只存 `strategy_name`，`summarize_signals` 用它聚合）；
  - trades/skips/open-positions 端点：**策略机器 id**（回测产物只存 id，展示名由注册表现查）。
  前端各自配套使用（选股页摘要行带显示名、回测页摘要行带 id + `strategy_name`），
  但**同名参数不同义**——契约测试各钉一条，避免后人误用。
- **白名单列必须在数据里存在**：`name`/`industry` 不在 `trades.parquet`/`picks.parquet` 中（实测列见
  §5），属展示期从 `stock_meta` 补的字段——按它们排序时在服务端 join 后再排（实测 36,525 行
  join 8ms、排序+切片 9ms，可忽略）。禁止出现「白名单有、数据没有同名来源」的列。

### 3.2 响应

```json
{ "items": [ … 当前页，已补全展示字段（name/industry 等）… ],
  "total": 1234, "offset": 0, "limit": 50 }
```

- `total` 为**过滤后**总数（前端据此渲染分页器）；
- `offset ≥ total` 时返回空 `items` 且 `total` 如实（前端负责回退到第 1 页，见 §7）；
- 未知 `execution_key` / 产物缺失 → 404（与现有端点同文案风格）。

### 3.3 稳定排序

分页正确性依赖全序。规则：

1. 默认序 = 产物固有序（picks 按 `(strategy, date, code)`；trades 按引擎写入序）；
2. 显式排序时，任何排序键之后都追加剩余固有序列作为 tiebreaker；
3. 排序实现走 polars（`sort(keys)` 稳定），不做 Python 层重排。

**默认序的实现前提（必须钉住）**：默认序不做排序，直接对 parquet 切片——依赖
「行序 = 写入序」且「多次读取行序一致」（写入侧已按固有全序排序；读取侧 polars 单文件扫描
保序）。这条是**分页正确性**的前提（否则用户翻页会看到重复/漏行），因此契约测试必须包含
「逐页翻完 == 全量序」（`limit=1` 遍历 + 与全量结果逐项相等），而不是只测单页。
若将来改用流式引擎或分片读取，需改为显式 `row_id` 列排序。

**显式排序的全序构造（P2 实施修正，勿回退）**：
- 回测三个数据集的排序键统一**追加 `_row`**（读取序行号）作为最终兜底——白名单键普遍存在
  并列值（同策略同代码多笔），只靠引擎的排序稳定性不构成契约；`_row` 使任何排序都是全序。
  `_row` 是内部列，响应里剔除（有测试钉住）。
- **补齐字段的 join 发生在切片之后**，而 join 不保证保序 → 取回本页后按同一全序再规范化一次
  （页内 ≤200 行，代价可忽略）。不这么做等于把「页内顺序正确」寄望于 join 的实现细节。
- 选股 picks 没有同类问题：其固有键 `(strategy,date,code)` 本身即全序，且补齐字段走字典查表
  （非 join）。

### 3.4 排序的实现细节（实测）

- **列不在索引文件里**（如 `industry`/`name`）：查询时左连接 `stock_meta`（5,901 行，进程内缓存）
  后再排序。实测（727,378 行最坏样本）：连接 8ms、按行业排序+切片 **62ms**、按代码排序 34ms、
  不排序仅切片 **1.4ms**、深页 58ms。全部 < 65ms，故**不做**预排序副本（用户已否决：可排序列太多，
  每列一份副本维护成本大于收益）。
- **空值位置**：`nulls_last=True`——数据缺失的行垫底，而不是插在列表开头。
- **中文排序口径**：采用引擎默认的 **Unicode 码点序**（零依赖、分组正确、次序稳定）。
  与前端旧行为（浏览器 `localeCompare('zh-Hans-CN')` 拼音序）的差异是「组的先后不同、组内相邻性相同」；
  不引入拼音库（实测两组次序已在评审中对照确认）。
- **选股明细白名单扩展**：在 `strategy/date/code` 之外加入 `industry`、`name`（同样走 join），
  使选股结果页的行业/名称列可排序（原先只有策略/日期/代码）。

## §4 产物索引（materialization）

### 4.1 索引文件

| 产物 | 文件 | 内容（**字段清单唯一来源**） | 适用 |
|---|---|---|---|
| 选股 | `selection/.index/picks.parquet` | `strategy, date, code`，按 `(strategy,date,code)` 排序 | 分页 |
| 选股 | `selection/.index/summary.json` | `schema_version, source_signature, total_picks, unique_codes, trade_days, signal_from, signal_to, by_strategy_date[{strategy,date,count,elapsed_seconds}]` | 列表 + KPI + 摘要表 |
| 回测 | `backtest/trades.parquet`、`skips.parquet`、`open_positions.parquet`（**已存在**） | 引擎写入（列见 `_run_backtest_worker`） | 分页 |
| 回测 | `backtest/.index/summary.json` | `schema_version, source_signature, window_from, window_to, trade_count, skip_count, open_position_count, by_strategy[{strategy, trade_count, skip_count, open_positions, realized_profit_sum, invested_notional_sum, unrealized_pnl}]` | 列表 + KPI |

`source_signature` = **来源文件 → mtime_ns 的字典**（而非单个数值）：回测索引的来源有三个
（`result.json`/`trades.parquet`/`skips.parquet`…），只对一个取 mtime 会在其余被替换时误判新鲜。
任一键值不匹配或 `schema_version` 不同即重建。组合级指标（`total_return_pct` 等）不进索引：
presenter 直读 `metrics.json`（408B）。索引**只存机器事实**，展示名与 meta 补全留在 presenter。

索引一律落**产物目录下的 `.index/` 隐藏子目录**（P1 实施修正）：若与产物同目录，
`artifacts` 列举会随「自愈是否已发生」漂移；产物目录的列举只收文件，子目录天然被跳过，
结果与生成时机无关。

回测的 `summary` 行含中文策略名与名称补全，属展示层——**决策**：索引只存「机器事实」
（trade_count/skip_count/open_positions/realized_profit_sum/invested_notional_sum/
unrealized_pnl/窗口日期），展示名与 meta 补全仍在 presenter 完成，列表端点很便宜（μs 级）。

### 4.2 生成时机

1. **新作业**：worker 落盘后调用同一 `ensure_*(key)`（幂等）——单一实现路径；
2. **存量产物（自愈）**：presenter 读到索引缺失/过期时生成一次；索引内含
   `source_mtime_ns`（源产物 mtime），不匹配即重建；
3. **降级**：索引生成失败（只读盘等）→ 该请求退回直读 JSON（功能不退化，只是慢），
   并记 WARNING 日志。**绝不因索引问题让端点 5xx。**
4. **取数来源（回测）**：优先 `trades.parquet`/`skips.parquet`/`open_positions.parquet`（引擎落盘即写，
   实测 869KB vs 15.9MB JSON）；产物缺这些文件时（0 笔成交的 run、旧版本产物）回落解析
   `result.json` 反建，签名同样覆盖被读的文件。
5. **失败后的行为（评估后简化）**：自愈失败不引入单独退避状态——失败时调用方本来就要走回落路径
   （直读 JSON），退避只能省下「再次尝试构建」里的小头，却多一份进程内状态与失效规则，
   收益≈0。代价如实记录：读失败期间每次打开工作台都按回落路径解析一次大 JSON（= 改造前的现状，
   不是回归）；日志留 WARNING 供排查。
6. **写序与撕裂窗口**：先写 parquet、后写 summary（summary 充当提交标记）。重建期间读者可能读到
   「新 summary + 旧 parquet」：分页端点的 `total` 与行数据都取自 parquet（自洽）；详情端点 counts
   取自 summary，极端窗口内两者可能相差一次重建——**已知可接受**（<1s，且只发生在存量自愈）。
7. **启动预热（已拍板：做）**：lifespan 起后台守护线程调用 `artifact_index.warm_indexes(executions_root)`，
   幂等补齐缺失/过期索引（有界：只扫 `objects/executions/` 一层；失败静默并记 WARNING，不影响服务启动）。
   成本移出请求路径——存量产物首次打开工作台即毫秒级。预热与请求路径共用 `ensure_*`（幂等），
   不存在两条实现。

### 4.3 并发与原子性

- 写索引一律 `tmpfile + os.replace`（原子）；两个请求同时生成时各自写入，内容相同，后者覆盖前者；
- 读索引不做锁（可见性由 `os.replace` 保证：要么旧要么新，不会读到半截文件）；
- **读取容错**：索引文件存在但读坏（截断/ParquetError/JSON 解析失败）→ 视为过期，删索引重建一次；
  仍失败则走回落路径。端点不得 5xx。

### 4.4 降级矩阵与三条不变式

索引是**纯加速器**：它缺席时的行为＝改造前的行为，绝不会更差。矩阵（每条有测试或实测支撑）：

| 场景 | 行为 | 依据 |
|---|---|---|
| `.index/` 整目录缺失/被删 | 回落直读原产物（= 老路径） | 测试 `falls_back_when_index_deleted` |
| 索引写不进去（只读盘/权限） | 每次请求走回落路径 + WARNING | 测试 `falls_back_when_index_unwritable` |
| 索引文件损坏（截断/非 parquet） | 删掉重建一次，仍失败则回落 | 测试 `recovers_from_corrupt_index` |
| 索引过期（来源签名变化） | 自动重建 | 测试 `rebuilds_after_source_change` |
| `schema_version` 升级 | 旧索引视为过期 → 重建 | 实测（v1→v2 重建 14 个存量） |
| 启动预热失败/进行中 | 静默跳过；该产物首次访问就地生成 | 设计 + 测试 |
| **源产物损坏**（在但读不了） | **500 + 明确文案**（可删除该记录后重跑） | 测试 `report_corrupt_source` |
| 源产物不存在 | 404 | 既有测试 |
| 并发重建 | 原子替换（后写覆盖） | 设计 |
| **并发删除产物** | 与索引写入共用一把写锁串行化；删除失败如实写入 `file_errors` | 测试 + 本轮修复 |

三条不变式：
1. **没有任何读路径以「索引存在」为前提**——两条路径（索引 / 直读）共用同一套查询与聚合实现，
   因此降级不会出现「数字不一样」；聚合实现只有一份（§10.8）。
2. **最坏回退不需要改代码**：删掉任意 `.index/` ＝ 关掉本次优化；批次各自独立可 `git revert`。
3. **索引里不存在「只有索引才知道」的信息**：一切可从原始产物重建。

并发细节（本轮修复的真实缺陷，勿回退）：
- **TOCTOU**：`is_file()` 与 `stat()` 之间文件可能被删（产物删除 × 预热/自愈）——stat 必须收进 try，
  异常一律视为「无产物」；预热循环整体包 try（尽力而为，任何单项异常不得逃逸，否则线程会死）。
- **写入与删除互斥**：`INDEX_WRITE_LOCK` 同时保护「索引写入」与「产物 rmtree」——否则遍历删除期间
  目录被写入会导致「删不掉但没人知道」（历史 bug：`except: pass` 吞掉失败，响应只报 deleted=0）。
- 删除失败必须进 `file_errors`（回测侧原先硬编码空数组，一并修正）。

## §5 端点变更（契约）

| 端点 | 变更 |
|---|---|
| `GET /api/selection-results/{key}` | 去掉 `picks`；新增 `counts: {total_picks, unique_codes, trade_days}`；其余（`result`/`artifacts`）不变 |
| `GET /api/selection-results/{key}/picks` | **新增**：分页契约 §3，过滤器 `strategies`/`dates` |
| `GET /api/backtest-results/{key}/report` | 去掉 `trades`/`skips`/`open_positions`/`equity`/`artifacts`（UI 未渲染）；新增 `trend`（§6）与 `counts: {trade_count, skip_count, open_position_count}`（与 §4.1 索引字段同名） |
| `GET /api/backtest-results/{key}/trades` | **新增**：分页 + `strategies` 过滤，排序白名单 `sell_date/profit/return_pct/buy_date/industry/code` |
| `GET /api/backtest-results/{key}/skips` | **新增**：同上（白名单 `signal_date/buy_date/industry/code`） |
| `GET /api/backtest-results/{key}/open-positions` | **新增**：同上（白名单 `buy_date/blocked_trading_days/unrealized_pnl/code`） |
| `GET /api/executions/active`（P5） | **新增**：`[{execution_id, job_type, status, progress_current, progress_total, progress_message, started_at, console_url}]` |
| `GET /api/selection-results`（P3） | 返回形状不变；每条改读 `.index/summary.json`（缺失→自愈；自愈失败→回落现行为解析 `signals.json`） |
| `GET /api/backtest-results`（P3） | 返回形状不变；每条改读 `backtest/.index/summary.json`（同上回落 `result.json`） |
| `GET /api/executions/{id}`（P4） | `result` 字段由「全量明细」变「摘要」——该端点无消费方 |

前端 `types/*.ts` 与 `services/*.ts` 同步（clean cutover，不做双形状兼容）。

### 5.1 列表端点读索引（P3）

- 现状（实测 0.29s/次、随历史条数线性恶化）：`list_selection_results_payload` /
  `list_backtest_results_payload` 每条历史调一次 `*_result_payload()`，各自整份解析
  `signals.json`（最大 13.2MB）/`result.json`（最大 15.9MB），只为算卡片上的日期/策略/条数。
- 改法：新增 `ensure_backtest_index(key, backtest_dir)`（与选股索引同模块、同自愈/原子写/降级语义），
  新作业由 backtest worker 落盘时写；列表端点只读索引（μs 级）。
- 回测索引字段清单见 §4.1（**唯一来源，本处不重复**）；组合级指标（`total_return_pct` 等）由
  presenter 直读 `metrics.json`（408B），不进索引。
- 副作用说明（已被启动预热覆盖）：常规路径下索引由**启动预热**（§4.2.7）在进程起来时补齐，
  列表请求不再付自愈成本；只有「预热失败/预热进行中/预热之后新增的产物」才在请求路径自愈一次
  （选股最坏 0.4s/13MB），之后为毫秒级。

### 5.2 `jobs.result_json` 瘦身（P4）

- 现状：回测 worker 把**完整 output**（36,525 笔 trades/skips/equity）传给 `ctx.succeed`，
  单行最大 **12.88MB**（`app.db` 18.1MB 的大头），而磁盘 `backtest/result.json` 已存同一份；
  `GET /api/executions/{id}` 全仓无消费方（前端只用 console/cancel）。
- 改法：`ctx.succeed` 只收摘要——`{metrics(截断为标量), trade_count, skip_count,
  open_position_count}`（选股 worker 传的本就是小字典，无需改）；明细继续只落盘。
- 旧行处理（可选、非必需）：一次性 `UPDATE jobs SET result_json = NULL WHERE length(result_json) > 100000`
  + `VACUUM`，由运维脚本执行；不做自动清理（避免修改历史审计行）。
- 验收：新作业 `length(result_json) ≤ 4KB`；`GET /api/executions/{id}` 返回 200 且 `result`
  含上述摘要字段；`app.db` 不再随每次回测增长 12MB。

## §6 趋势序列服务端化（P2）

现状：`TradeReturnTrendCards.tsx` 在前端用**全量 trades** 计算「按卖出日聚合的日收益/累计收益」。
改为后端 `trend` 聚合（polars 按 `(strategy, sell_date)` 求和），前端只画图。

- 公式沿用前端既有口径（逐字迁移，不新造）：`dailyCost = Σ total_cost`（缺失时 `buy_price×shares`）、
  `dailyProfit = Σ profit`、`dailyReturnPct = dailyProfit/dailyCost×100`、累计同法；
- 响应形状：`trend: [{strategy, strategy_name, points: [{date, dailyCost, dailyProfit, dailyReturnPct, cumulativeCost, cumulativeProfit, cumulativeReturnPct, tradeCount, winCount, lossCount}]}]`，
  首点保留 `date="起点"` 基线（前端现行为）。**`strategy_name` 必填**：前端卡片标题取
  `trades.find(t => t.strategy_name)?.strategy_name || strategy`（现为 trades 内查找），trades 不再返回后
  若无此字段，标题会退化成英文 id。
- **体积上界**：`点数 = 策略数 × 交易日数`（与成交笔数无关）。实测 14 策略 × 1,250 交易日 ≈
  3.5k 点 = 3.8MB 原始 / **16.7KB gzip**——线上代价可忽略，服务端产出约 50ms（parquet 分组）。
  若将来确实嫌大，再改为按 `strategies` 过滤（前端切策略时重取），当前不做以保持「一次请求渲染整页」。
- 前端 `TradeReturnTrendCards` 改为消费 `trend`（保留现有配色、图例、tooltip 字段），
  **删除前端自算逻辑**（不得两处并存）。
- **取数来源**：`trades.parquet`（列投影 + 分组，实测 20ms 级）；缺失（0 笔成交的 run、旧产物）
  时回落 `result.json` 的 trades。`total_cost` 在两种产物里都不存在 → 口径恒为 `buy_price × shares`
  （与前端旧兜底一致，已用测试对齐：Fixture 与真实样本的数值逐点核对）。
- **基线点（date="起点"）仍由前端插入**：纯展示，服务端不产（避免把渲染细节写进契约）。
- 明细白名单补全（与前端既有可排序列对齐，只能多不能少）：open-positions 增加
  `mark_price`、`unrealized_return_pct`。

## §7 前端统一分页器（P1 交付，P2 复用）

两个单元，职责单一：

- `frontend/src/hooks/usePagedQuery.ts`：状态机（offset/limit/sort/filters/loading/error/total/items），
  负责请求编排：`AbortController` + 序号守卫（仓库既有 `useKline` 模式）、参数变化重置到第 1 页、
  卸载清理、失败保留上一页数据并把错误暴露给调用方；
- `frontend/src/components/PagedTable.tsx`：antd Table 包装——受控 `pagination`（`current/pageSize/total/showSizeChanger`）、
  `onChange` 分流（翻页/换页大小/排序）、错误 Alert、空态；列定义由调用方传入。

约束（鲁棒性）：
- 过滤器变化 → `offset=0`；`total` 变小导致当前页越界 → 自动回退最后一页并重取；
- 请求失败不清空已有行（避免"白屏式失败"）：错误经单条 Alert 呈现（`PagedQuery.error`），不写日志行；
- 排序状态与后端 `sort_by/sort_dir` 一一对应，前端不做本地排序（避免"页内排序"错觉）。

## §8 运行中任务可见性（P5）

- 新端点 `GET /api/executions/active`（读 `jobs` 表，`status IN ('queued','running','cancelling')`，
  进度复用 `parse_progress` 解析 `[PROGRESS]` 行，与 console 同口径；实测最大作业 5,554 行日志、
  全量读取 9ms——不需要给 `jobs` 加进度列）；
- **幽灵任务不会残留**：进程重启时 lifespan 会把 `queued/running` 一律标 failed（既有行为），
  因此徽标只反映本进程存活期间的作业；
- AppShell 轮询（10s；`document.hidden` 时暂停；有任务时显示徽标与下拉列表，点击进控制台）；
- 控制台页移入 AppShell 子路由（现为 AppShell 之外的独立路由），保留现有视觉；
- 提交作业由 `window.location.href` 整页跳转改为 `navigate(console_url)`（SPA 内导航，保留历史栈）。

## §9 C/D 项

### 9.1 计数口径（P6）

- 现：`totalPickCount` 把 Σ(策略×日期) 标成「N 只」——跨日批量 run 显示过 163,412（全市场 5,814）；
- 改：`counts` 三值驱动——单日显示「N 只」；多日显示「N 条信号 · M 只（去重）· K 个交易日」；
- 数据源：`summary.json`（`total_picks/unique_codes/trade_days`），不额外计算。

### 9.2 报告首屏（P7）

趋势图（两张）上移到「策略对比」之后；交易规则文案默认折叠（`Collapse`，不持久化展开态）；KPI 保持 4 张。

**验收（实测通过）**：1440×900、滚动位置 0 时「交易收益走势」标题 top=662px（< 视口 900）✓；
交易规则默认折叠 ✓ 且可展开；区块顺序 = KPI → 走势 → 策略对比 → 明细 → 跳过 → 未平仓。

### 9.3 前端拆包（P8）

`/selections`、`/backtests/*`、`/market-data` 路由级 `React.lazy`（K 线页已是）；vite
`manualChunks` 把 `recharts` 单独成 chunk。目标：首访主包 ≤1.0MB（现 1.71MB）。

**验收（实测通过）**：主包 **1,711KB → 654KB**（gzip 530KB → 207KB）；recharts 随
`BacktestReportPage` 分包（gzip 117KB，仅打开报告时加载）；选股页首访 JS+CSS 合计 **347KB**；
6 条路由浏览器实测全部正常渲染、无 chunk 404；产物仍带 hash 文件名。
实现方式：路由级 `React.lazy` 覆盖全部页面（框架 AppShell 保持同步），未再手工配 manualChunks——
recharts 只被报告页引用，懒加载后自然落入其 chunk。

### 9.4 零散（P9）

- `_stock_meta_map()`（无缓存/无列裁剪）并入 `_stock_meta_by_code()`（60s TTL、列裁剪），单一实现；
- 报告响应去掉 UI 未渲染字段（`equity`、`artifacts`）——与 §5 一并落地；
- `storage.stock_count` 口径（**已拍板**）：取 `stock_meta.parquet` 行数（股票池规模）；`local_file_count`
  仍为 `bars/*.parquet` 文件数。实测差异立刻可见：**5,901 vs 5,814**（87 只未落盘/已退市）——
  两者不再是同一个 glob，差异本身即缺口信号。

## §10 鲁棒性清单（验收项）

1. 索引缺失/过期/损坏 → 重建；重建失败 → 降级直读，端点不 5xx；
2. 并发生成索引 → 原子替换，无半截文件；
3. 分页参数非法 → 明确报错、不静默回落默认值：非白名单 `sort_by` → 400；`offset`/`limit` 越界 → 422；
4. `offset ≥ total`、空过滤结果、策略全不匹配 → 空页 + 正确 total；
5. 增量一致性：来源文件签名（`mtime_ns`）变化后索引自动重建，`total` 与内容同步变化。
   已知边界：备份还原等**逐位恢复且保留 mtime** 的改动无法识别——属可接受风险（本地工具，
   真出现时删 `.index/` 即恢复）；
6. 前端：切页/切过滤/卸载不发悬挂请求（abort + 序号守卫）；失败保留旧数据；
7. 所有新增端点有契约测试（形状 + 边界 + 404/400/422）。
8. **聚合逻辑单一实现源**：选股摘要聚合（presenter `_selection_summary` 与索引构建）、回测按策略汇总
   （`_backtest_summary` 与索引构建）各只保留一份实现——P1 已留下一处重复，P3 收口；presenter 的
   回落路径复用索引模块的聚合函数。
9. **部署耦合**：详情端点去掉 `picks` 后前后端必须同版本发布（单用户本地工具，不做版本协商）；
   `index.html` 经 304 重验证 → 浏览器刷新即换新哈希 bundle。回滚 = 回退代码；`.index/` 是派生物，
   可整目录删除，删除后行为退回「直读 JSON」。
10. **故障注入**（随各批测试落地）：索引写失败（只读盘）、索引文件损坏、源产物缺失三种情况下，
    端点均返回正确数据（走回落路径），不 5xx。
11. **响应形状变更必须进 ETag**（实测踩过）：报告端点用「产物身份（mtime+size）」做 ETag 时，
    后端改了返回结构但产物没变 → 浏览器 304 复用**旧形状的缓存体** → 前端按新结构解析直接崩。
    规则：响应结构变化必须 bump `BACKTEST_REPORT_ETAG_VERSION`（进 ETag 值），有测试钉住。
12. **换排序回第 1 页**（统一分页器行为）：否则「点了排序，看到的却是第 2 页」，首行不是最值——
    浏览器实测发现并修正。

## §11 测试与验收

- 后端：pytest 契约与边界（`tests/interfaces/`）、索引单测（`tests/infrastructure/`）、
  worker 落盘后索引存在（`tests/app/`）；
- 前端：无测试框架 → 浏览器实测（翻页/过滤/排序/页大小/错误恢复），并在 review-backlog 记录；
- 性能验收（本机，区分首次与稳态）：
  - **稳态**：选股 `picks` 分页 ≤0.3s / ≤100KB；回测 trades 分页 ≤0.3s；两个列表端点 ≤50ms；
    详情端点 ≤0.2s；新作业 `jobs.result_json` ≤4KB。
  - **首次（含存量自愈）**：选股最坏样本 ≤1.5s；回测 ≤1.0s。自愈只发生一次且**索引落盘即持久**
    （进程重启不重复，除非源产物 mtime 变化）。

## §12 分批与顺序（用户确认）

| 批次 | 内容 | 依赖 |
|---|---|---|
| 1 | P1 选股分页（索引 + 端点 + 统一分页器 + 计数口径 P6 的一并落地） | ✅ 已完成 |
| 2 | P3 + P4 列表与 jobs 瘦身（复用 §4 索引） | 批次 1 的索引模块 ✅ |
| 3 | P2 回测明细分页 + 趋势迁移（复用分页器） | 批次 1 的分页器 ✅ |
| 4 | P5 运行中任务可见性 | — |
| 5 | P7/P8/P9 | 批次 3 完成后（报告页结构稳定） |

## §13 风险与对策

| 风险 | 对策 |
|---|---|
| 索引与源产物漂移 | `source_mtime_ns` 校验 + 自愈 + 降级 |
| 前端分页器成为"又一个抽象层" | 只有两个调用方（picks、trades/skips/open），接口收窄到 props（列 + fetcher + filters），不做插件化 |
| 明细表排序语义变化（原先本地排序全量） | 排序键白名单与服务端实现，契约测试钉住；前端不再本地排序 |
| 存量产物首次访问慢 | 由启动预热（§4.2.7）在进程起来时补齐；实测 14 个存量 run 全部自动建好索引，请求路径零自愈 |
