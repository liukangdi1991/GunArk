# TrendRadar V2 清空重建设计

## 目标

TrendRadar V2 是对当前私人量化研究工作台的一次破坏性重构。目标是把现有混合在一起的模块结构，重建成边界清晰的领域架构：行情数据、策略管理、信号生成、回测、存储、任务系统、API 与报告展示各自有明确职责。

本系统可以在重构期间暂停使用。历史运行数据不重要，不做迁移。

## 已确认决策

- V2 不兼容旧选股结果、旧回测结果、旧 `storage/app.db`、旧 artifact、旧 `db/` 行情 parquet 文件。
- V2 初始化时会清空旧运行数据，并创建新的运行目录结构。清空必须由显式命令触发，普通 Web/API 启动不得自动删除旧数据。
- 现有策略算法、指标公式、回测交易规则可以迁移到新架构。
- Web 工作台仍然是主界面，默认继续使用 FastAPI + React，除非后续设计明确调整。
- 所有可变运行数据都放到 `storage/` 下。
- 策略组是一等运行时配置，不只是前端展示分类。
- 选股、批量选股、选股回测都通过同一个策略解析器解析策略。
- 回测只消费标准 `SignalSet` artifact，不直接理解选股服务的内部输出格式。

## 非目标

- 不做旧选股/回测 artifact 的兼容适配层。
- 不迁移历史执行记录。
- 不迁移旧本地行情 parquet 文件。
- 不做多用户权限模型。
- 不拆微服务。
- V2 首版不强制引入 Redis、Celery、PostgreSQL。
- 架构重构期间不重设策略算法。
- 除支持策略组和新执行模型所必需的改动外，不做大规模前端重设计。

## 破坏性初始化安全策略

V2 是清空重建，但删除动作必须受控。实现时必须满足：

- 普通服务启动只检查并初始化缺失目录，不自动删除已有运行数据。
- 清空旧运行数据只能由显式命令触发，例如 `trendradar init-v2 --reset-runtime` 或等价管理脚本。
- 清空命令必须要求二次确认，或要求传入明确的确认参数，例如 `--confirm-reset`。
- 清空范围只允许包含 V2 运行根目录下的 `storage/app.db`、`storage/objects/`、`storage/market/` 和旧根目录 `db/`。
- 清空命令执行前应打印将删除的路径列表。
- 部署脚本不得在升级时隐式清空数据；清空只属于首次 V2 重建或用户主动 reset。

## 目标架构

```text
trendradar/
  domain/
    market/
    strategy/
    signal/
    backtest/
    research/

  infrastructure/
    runtime.py
    storage/
    tushare/
    filesystem/

  app/
    jobs/
    services/

  interfaces/
    api/
    reports/
```

### 分层职责

`domain/` 存放业务模型、协议和确定性规则。这里的代码尽量不依赖 FastAPI、SQLite 或具体文件路径。

`infrastructure/` 存放基础设施适配器，包括 SQLite、parquet 文件、Tushare、运行时路径、checksum 和 artifact 存储。

`app/` 存放用例编排逻辑，例如提交行情同步任务、执行选股、执行选股回测、取消任务、组装结果元信息。

`interfaces/` 存放 FastAPI 路由、请求/响应 schema，以及前端使用的报告展示模型。

## 运行数据布局

所有可变运行数据统一存放在 `storage/`。

```text
storage/
  app.db
  market/
    bars/
      000001.parquet
      600519.parquet
    calendar.parquet
    stock_meta.parquet
  objects/
    executions/
      <execution_key>/
        manifest.json
        lineage.json
        selection/
        backtest/
    jobs/
      <job_id>/
        state.json
        console.log
```

V2 不再使用根目录下的 `db/`。初始行情数据通过 V2 行情同步流程重新拉取。

## 核心领域流程

```text
Tushare
  -> MarketDataStore
  -> StrategyResolver
  -> StrategyRunner
  -> SignalSet
  -> SignalRepository
  -> BacktestRunner
  -> BacktestResult
  -> ArtifactStore + MetadataStore
  -> API/Web Report
```

## 策略组

策略组是可编辑的运行时对象，存储在 SQLite 中。

### 数据模型

```text
StrategyGroup
  id: str
  name: str
  description: str
  enabled: bool
  sort_order: int
  created_at: datetime
  updated_at: datetime

StrategyGroupMember
  group_id: str
  strategy_id: str
  sort_order: int
```

### 规则

- 当存储为空时，系统初始化 `default` 策略组。
- 迁移过来的现有策略全部加入 `default`。
- V2 清空重建后，旧 `configs.json` 不作为运行时权威数据源；它只作为迁移策略默认参数时的参考输入。
- `default` 不能删除。
- 删除非 default 组不会删除策略定义。
- 删除非 default 组时，其组内策略仍保留在其他组里；如果某个策略只属于被删除的组，则自动移回 `default`。
- 一个策略可以属于多个组。
- 多个被请求策略组同时包含同一策略时，按用户请求组顺序确定该策略的 primary group；结果展示保留全部 `group_ids`，排序使用 primary group 的组顺序和组内成员顺序。
- 禁用的策略组在按组执行时会被忽略。
- 禁用的策略在按组执行和按策略直接执行时都会被忽略。
- 策略 ID 必须是稳定的机器标识，不能依赖中文显示名。

### 策略注册表

策略定义来自代码注册表，不从数据库动态加载 class。

```text
domain/strategy/
  models.py
  protocol.py
  registry.py
  resolver.py
  adapter.py
  selectors/
```

`registry.py` 注册所有可用策略。

`resolver.py` 把请求中的策略组 ID 和策略 ID 解析成最终有序策略列表。

`adapter.py` 包装现有 selector 实现，在引入 V2 稳定策略协议的同时复用当前算法行为。

### 注册机制与策略 ID 映射

V2 首版采用显式注册表，不做自动模块扫描。每一个 `StrategyDefinition` 表示一个可运行的策略实例，而不是一个 selector class。这样同一个 selector class 可以注册为多个不同策略实例，并拥有不同 `strategy_id`、显示名和默认参数。

现有 `configs.json` 的初始映射建议如下：

```text
B1战法 -> bbi_kdj_b1
SuperB1战法 -> super_b1
补票战法 -> bbi_short_long
填坑战法 -> peak_kdj
上穿60放量战法 -> ma60_volume_wave
多空平衡选股策略 -> zxdkx_balance
B1战法（V2） -> perfect_b1_v2
完美B1 -> perfect_b1_volume_stepdown
暴力K战法 -> big_bullish_volume
倍量多空平衡策略 -> volume_spike_balance
```

迁移规则：

- `class` 映射到代码注册表里的 selector class。
- `alias` 只作为显示名，不作为主键。
- `params` 导入到 `strategy_settings.params_json` 作为初始用户参数覆盖。
- `activate` 导入到 `strategy_settings.enabled`。
- 所有 active selector 默认加入 `default` 组。
- `default_strategies` 不表示 `default` 组成员；它可作为前端默认勾选项导入为 `default_run_strategy_ids`，首版也可以忽略。

### 策略定义与参数来源

策略 class 和参数 schema 由代码注册表提供，运行时数据库保存用户配置。

- 代码注册表定义 `strategy_id`、显示名、策略 class、参数 schema、默认参数和描述。
- SQLite 保存策略启用状态、用户覆盖后的默认参数、策略组关系和排序。
- 旧 `configs.json` 中的策略参数可以在 V2 初始迁移时导入一次，但导入后不再作为运行时权威来源。
- 每次选股或回测的 lineage 必须保存当时实际使用的参数快照。
- 如果策略 class 从代码中移除，但数据库仍有该策略配置，系统应在策略列表中标记为 unavailable，而不是静默忽略。

### V2 策略协议与 Legacy Adapter

V2 策略协议固定为面向 `SelectionContext` 的接口：

```python
class SelectionStrategy(Protocol):
    definition: StrategyDefinition

    def select(self, context: SelectionContext) -> SelectionResult:
        ...
```

`SelectionContext` 至少包含：

```text
trade_date: date
market_data: pl.DataFrame
candidate_codes: list[str] | None
```

`SelectionResult` 至少包含：

```text
strategy_id: str
strategy_name: str
trade_date: date
selected_codes: list[str]
elapsed_seconds: float
```

现有 selector 不直接实现该协议。V2 通过 `LegacySelectorAdapter` 桥接：

```text
LegacySelectorAdapter
  -> 持有 selector 实例
  -> 持有现有 runner 或 runner factory
  -> 接收 SelectionContext
  -> 调用 runner.run_selection(date_obj, data_table, get_data_dict)
  -> 返回 SelectionResult
```

这样可以先迁移架构和接口，同时保留现有 selector 与 runner 的算法行为。

### 选股语义

选股、批量选股、选股回测请求都接受：

```json
{
  "groups": ["default", "trend"],
  "strategies": ["peak_kdj"]
}
```

解析规则：

- 如果 `groups` 和 `strategies` 都为空，使用 `default` 组内启用策略。
- 如果只传 `groups`，运行这些启用组里的启用策略。
- 如果只传 `strategies`，直接运行这些启用策略。
- 如果两者都传，运行“启用组成员 + 启用直接策略”的并集。
- 使用 `strategy_id` 去重。
- 最终顺序必须稳定：按用户请求组顺序和组内成员顺序排序；直接指定但不属于任何请求组的策略排在组解析结果之后；最后用策略显示名打破并列。

## 行情数据

V2 使用统一的行情数据存储抽象。

```text
domain/market/
  models.py
  data_store.py
  calendar.py
  stock_meta.py
```

`MarketDataStore` 提供：

```text
load_bars(codes, start, end, columns)
latest_trade_date()
trading_dates(start, end)
stock_meta(codes)
```

行情数据层负责：

- Parquet 文件 schema。
- 股票代码标准化。
- 交易日历访问。
- 最新交易日计算。
- 缺失行情数据语义。
- 股票元数据查询。

V2 首版日线 bar schema 至少包含：

```text
code: str
date: date
open: float
high: float
low: float
close: float
volume: float
amount: float | null
adj_factor: float | null
is_suspended: bool | null
```

价格口径必须在 market metadata 中明确记录。V2 首版默认沿用当前 Tushare 拉取口径；如果后续支持前复权/后复权/不复权切换，必须把复权类型写入 market sync lineage 和 backtest lineage。

选股和回测代码不得直接扫描原始 parquet 文件。

### 行情同步与 Tushare 适配

行情同步是写流程，不放在 `MarketDataStore` 读接口里硬塞。V2 分工如下：

```text
infrastructure/tushare/client.py      # Tushare client 初始化、网络环境、token 校验
infrastructure/tushare/syncer.py      # 拉取日线、限流识别、重试、冷却退避
infrastructure/tushare/stocklist.py   # 股票列表同步与板块过滤
app/services/market_service.py        # 编排同步任务、进度、日志、取消
```

现有生产逻辑需要迁移：

- Tushare IP 限流识别和冷却退避。
- 3 次重试与分级退避。
- 北京时间 16:00 cutoff 规则，用于判断当前可用最新交易日。
- 增量同步：如果本地单票最新交易日已经覆盖目标结束日，则跳过。
- 股票列表同步和原子写入；V2 写入 `storage/market/stock_meta.parquet`。
- 板块过滤：创业板、科创板、北交所等过滤选项继续由同步请求控制。
- 同步任务必须支持取消，worker 在每只股票处理前检查取消状态。

写入规则：

- 单票 parquet 写入先写 `.tmp` 文件，成功后 atomic replace。
- 同步过程中不得让 selection/backtest 读取 `.tmp` 文件。
- 一次只允许一个 `market_data_sync` job 运行。
- 同步完成后写入 `market_sync_runs`，并将 market sync execution 与后续 selection execution 通过 `execution_links` 关联。


## 信号模型

`SignalSet` 是选股与回测之间的合同。

```text
SignalSet
  execution_key: str
  signal_from: date
  signal_to: date
  strategy_groups_snapshot: list[StrategyGroupSnapshot]
  strategies_snapshot: list[StrategySnapshot]
  signals: list[StrategySignal]

StrategySignal
  strategy_id: str
  strategy_name: str
  group_ids: list[str]
  signal_date: date
  codes: list[str]
```

无论是单日选股还是批量选股，选股流程都写入同一种标准信号 artifact。

`signals.json` 使用数组结构，不使用策略名作为动态 key：

```json
{
  "schema_version": "2.0",
  "execution_key": "20260709_120000_selection",
  "signal_from": "2026-07-01",
  "signal_to": "2026-07-09",
  "signals": [
    {
      "strategy_id": "b1",
      "strategy_name": "B1战法",
      "group_ids": ["default"],
      "primary_group_id": "default",
      "signal_date": "2026-07-09",
      "codes": ["000001", "600519"]
    }
  ]
}
```

## 选股 Artifact Schema

```text
storage/objects/executions/<execution_key>/
  manifest.json
  lineage.json
  selection/
    signals.json
    picks.parquet
    summary.json
```

`manifest.json` 标识一次执行：

```json
{
  "schema_version": "2.0",
  "execution_key": "20260709_120000_selection",
  "execution_type": "selection",
  "created_at": "2026-07-09T12:00:00",
  "status": "success"
}
```

`lineage.json` 记录请求和解析后的输入：

```json
{
  "requested_groups": ["default"],
  "requested_strategies": ["peak_kdj"],
  "resolved_strategies": [
    {
      "id": "b1",
      "name": "B1战法",
      "group_ids": ["default"],
      "class_name": "SuperB1Selector",
      "params": {}
    }
  ],
  "market_data": {
    "from": "2026-01-01",
    "to": "2026-07-09"
  }
}
```

`signals.json` 是机器可读的回测输入。`picks.parquet` 面向结果展示。`summary.json` 保存按策略和按组聚合的数量与耗时。

## 回测模型

回测层拆分为编排组件和确定性模拟组件。

```text
domain/backtest/
  models.py
  config.py
  runner.py
  engine.py
  execution.py
  portfolio.py
  metrics.py
```

职责：

- `runner.py` 使用 `SignalSet`、行情数据和 `BacktestSpec` 编排一次回测。
- `engine.py` 推进交易日，协调买入、卖出、组合估值和跳过记录。
- `execution.py` 负责成交价、费用、滑点、涨停和跌停行为。
- `portfolio.py` 负责现金、持仓、仓位计算和再入场规则。
- `metrics.py` 根据权益曲线和交易记录计算汇总指标。

V2 初版必须保持当前交易规则行为：

- 信号日是 `T`。
- 买入尝试发生在 `T+1 open`。
- 固定持仓周期与当前实现保持等价。
- 继续支持长期多空线止损和 10 日低点止损。
- 继续支持涨停拒绝买入和跌停拒绝卖出。
- 如果迁移时仍存在不限资金和真实现金两种模式，则两者都要继续支持。


### Pandas/Polars 数据框架策略

V2 目标是让行情、选股和信号层以 Polars 为主要 DataFrame 技术栈。回测迁移分阶段进行：

- `MarketDataStore` 对外返回 Polars DataFrame。
- 选股 runner 和 selector adapter 使用 Polars 输入。
- 回测 runner 初版可以在边界将 Polars 转成 pandas，以保留现有回测行为并降低重写风险。
- 回测内部最终目标是迁移到 Polars，但行为等价测试优先于技术栈统一。
- 所有从 pandas 到 Polars 的迁移都必须有涨跌停、止损、持仓周期和资金模式测试保护。

## 回测 Artifact Schema

```text
storage/objects/executions/<execution_key>/
  manifest.json
  lineage.json
  backtest/
    trades.parquet
    skips.parquet
    equity.parquet
    metrics.json
    report.json
```

回测 lineage 记录：

- 来源选股执行 key。
- 请求的策略组和策略。
- 解析后的策略快照。
- 信号执行 key。
- 行情数据日期范围。
- 交易策略。
- 资金模式。
- 每票金额。
- 回测配置快照。

## SQLite Schema

V2 从新 schema 开始。

```text
executions
  id
  execution_key
  execution_type
  status
  created_at
  started_at
  finished_at
  manifest_key
  lineage_key

execution_items
  id
  execution_key
  item_type
  item_key
  item_name
  params_json
  metrics_json

artifacts
  id
  execution_key
  artifact_type
  storage_key
  mime_type
  size_bytes
  checksum
  created_at

execution_links
  id
  source_execution_key
  target_execution_key
  link_type
  created_at

jobs
  id
  job_id
  job_type
  status
  created_at
  started_at
  finished_at
  request_json
  result_json
  error_message

job_logs
  id
  job_id
  sequence
  timestamp
  level
  message

strategy_groups
  id
  name
  description
  enabled
  sort_order
  created_at
  updated_at

strategy_group_members
  group_id
  strategy_id
  sort_order

strategy_settings
  strategy_id
  enabled
  params_json
  updated_at

market_sync_runs
  id
  execution_key
  start_date
  end_date
  stock_count
  skipped_latest
  empty_count
  failed_count
  created_at
```

`execution_links` 的方向固定为：`source_execution_key` 是上游依赖，`target_execution_key` 是下游产物。例如：

```text
selection_execution -> backtest_execution  link_type = "backtest_uses_selection"
market_sync_execution -> selection_execution  link_type = "selection_uses_market_sync"
```


### SQLite 约束与索引原则

V2 DDL 必须明确约束和索引，不只停留在字段列表：

- `executions.execution_key` 唯一。
- `artifacts.storage_key` 唯一。
- `artifacts(execution_key, artifact_type)` 唯一，除非某类 artifact 明确允许多文件。
- `strategy_groups.id` 主键。
- `strategy_group_members(group_id, strategy_id)` 唯一。
- `strategy_settings.strategy_id` 主键。
- `execution_links(source_execution_key, target_execution_key, link_type)` 唯一。
- `jobs.job_id` 唯一。
- `job_logs(job_id, sequence)` 唯一。
- `manifest_key` 和 `lineage_key` 是 artifact storage key，不是绝对路径。
- SQLite 连接必须启用 `PRAGMA foreign_keys = ON`。
- SQLite 建议启用 WAL，以改善 Web 查询和后台任务写入并发。

`params_json` 和 `metrics_json` 使用 JSON 字符串是有意简化，适合私人工作台；V2 不继续沿用旧版 key-value 参数子表。

### 并发与一致性

V2 首版按单机工作台设计，并发策略如下：

- 同一时间只允许一个行情同步 job 运行。
- selection 和 backtest 可以并发运行，但每次执行必须使用独立 `execution_key` 目录。
- artifact 写入必须先写临时文件，再 atomic replace。
- artifact metadata 只在必要文件全部写入成功后注册。
- 策略组和策略设置更新必须在 SQLite transaction 中完成。
- selection/backtest 启动时解析并快照策略组和策略参数；运行过程中策略组变更不影响已启动任务。
- market sync 正在写入某只股票文件时，selection/backtest 只能读取上一版完整 parquet，不能读取 `.tmp`。

## 任务系统

V2 初版可以继续保持单进程、线程池式执行，但任务状态必须隔离在 app 层接口后面。

```text
app/jobs/
  executor.py
  state.py
  logs.py
```

任务层负责：

- 排队。
- 运行。
- 取消。
- 进度更新。
- 控制台日志。
- 将 job 与产出的 execution result 关联起来。

这样可以避免 FastAPI 路由直接管理 worker 线程和文件状态。


### 现有任务系统迁移策略

现有 `web/services/execution_service.py` 的任务机制不直接丢弃，V2 先迁移并收敛命名：

- `ExecutionState` 迁移为 `JobState`。
- `ExecutionContext` 迁移为 `JobContext`。
- `ThreadPoolExecutor(max_workers=2)` 的单机执行模型首版可以继续沿用。
- `storage/objects/jobs/<job_id>/state.json` 和 `console.log` 结构继续沿用。
- `cancelling` 状态和 worker 主动检查取消的协作机制继续沿用。
- `job_logs` 表提供结构化日志索引，`console.log` 保留为前端控制台文本流；首版可以双写。

现有任务类型映射到 V2 `job_type`：

```text
selection_latest -> selection_latest
selection_single -> selection_single
selection_batch -> selection_batch
backtest -> backtest
backtest_from_selection -> backtest_from_selection
selection_backtest -> selection_backtest
market_data_sync -> market_data_sync
```

V2 可以重写服务层编排，但不应重新发明进度、日志、取消和 job 状态持久化机制。

## API 与前端

FastAPI 和 React 继续作为工作台界面。

策略组 API 至少支持：

```text
GET    /api/strategy-groups
POST   /api/strategy-groups
PATCH  /api/strategy-groups/{group_id}
DELETE /api/strategy-groups/{group_id}
POST   /api/strategy-groups/{group_id}/members
DELETE /api/strategy-groups/{group_id}/members/{strategy_id}
PATCH  /api/strategy-groups/{group_id}/members/{strategy_id}
POST   /api/strategy-groups/{group_id}/members/reorder
GET    /api/strategies
PATCH  /api/strategies/{strategy_id}/settings
```

运行任务至少支持：

```text
POST /api/executions
```

选股、批量选股、选股回测的执行 payload 都包含 `groups` 和 `strategies`。

前端需要支持：

- 策略组管理。
- 按组展示策略选择器。
- 按策略组运行。
- 按一个或多个单独策略运行。
- 按“策略组 + 单独策略”的并集运行。
- 结果摘要按策略组和策略展示。


### 前端迁移范围

前端保持工作台形态，但需要明确这些改动：

- `SelectionWorkspacePage`：策略选择器改为“策略组 + 单独策略”的混合选择。
- `BacktestWorkspacePage`：选股回测请求支持 `groups` 和 `strategies`。
- 新增或扩展策略组管理视图，支持创建、重命名、禁用、删除和排序。
- 新增策略设置能力，支持启用/禁用策略和调整默认参数覆盖。
- `StrategySnapshots` 和结果摘要组件展示策略所属组、primary group 和参数快照。
- `frontend/src/services` 和 `frontend/src/types` 增加 `StrategyGroup`、`StrategyDefinition`、`StrategySettings` 类型。

## 破坏性重建初始化

V2 初始化会删除旧运行数据，并创建新的 V2 运行目录结构。删除动作只能通过显式 reset/init 命令执行，不能发生在普通服务启动流程中。

初始化流程：

1. 校验用户显式传入 reset 确认参数。
2. 打印即将删除和重建的路径列表。
3. 删除旧运行数据。
4. 确保 `storage/` 存在。
5. 初始化全新的 V2 `storage/app.db`。
6. 创建 `storage/market/`、`storage/objects/`、`storage/objects/jobs/`。
7. 从代码注册可用策略。
8. 创建 `default` 策略组。
9. 将每个已注册策略加入 `default`。
10. 选股或回测运行前必须先完成行情同步。

V2 首版需要在 README 和部署说明中明确写出这次重构的破坏性。


## 错误处理与可观测性

执行状态枚举至少包括：

```text
queued
running
success
failed
cancelling
cancelled
```

artifact 状态规则：

- 执行开始后可以创建 execution 目录。
- 成功执行写入完整业务 artifact，并注册 metadata。
- 失败执行保留 `manifest.json`、`lineage.json` 和 `error.json`，但不注册不完整业务 artifact。
- 取消执行写入 `cancelled` 状态，不等同于 `failed`。
- job 日志必须保留，便于定位失败原因。

日志规则：

- `console.log` 是用户可读文本流。
- `job_logs` 是结构化日志表。
- 首版允许双写，后续可用结构化日志重建控制台输出。

## 部署变更清单

V2 存储布局变化会影响 Docker 和二进制发布包：

- Docker 不再单独挂载根目录 `db/`。
- 运行数据统一挂载到 `storage/` 对应的数据目录。
- `deploy/data/db` 不再作为行情种子目录。
- 部署脚本不得在升级时自动执行 reset。
- 首次 V2 初始化需要显式 reset/init 命令。
- 二进制包 init 脚本需要创建 `storage/market/bars/`、`storage/objects/` 和 `storage/objects/jobs/`。
- README 需要明确：V2 首次启动后必须重新同步行情。

## 测试策略

每个核心切片都应该先写测试，再实现。

必要测试范围：

- 破坏性初始化只能通过显式确认命令触发，普通启动不会删除数据。
- 策略组创建、删除和 default 组保护。
- 策略解析器覆盖按组、按策略、混合、空请求、禁用组、禁用策略、多组重复策略和 primary group 排序。
- MarketDataStore 能读取和原子写入 V2 `storage/market/bars/` 布局。
- Tushare 同步保留限流识别、重试退避、16:00 cutoff、增量跳过和取消检查。
- `SignalSet` 按固定 JSON 数组结构序列化与反序列化。
- 选股写入 V2 选股 artifact。
- 选股回测使用和选股相同的策略解析器。
- 回测消费 `SignalSet`，不消费旧信号文件。
- 回测交易规则保护当前涨跌停、止损、持仓规则行为。
- `execution_links` 能连接选股和回测结果，并验证 source/target 方向。
- SQLite 约束、唯一索引、foreign keys 和 WAL 初始化可验证。
- 策略注册表能处理同 class 多策略实例和旧 configs.json 初始导入。
- 前端类型和 API schema 覆盖策略组、策略设置、混合运行请求。
- 全新初始化能创建默认存储、default 策略组和策略成员关系。

## 迁移计划

不从旧运行数据迁移任何内容。

实现期间可以暂时保留旧模块。等 V2 路径接入 API 并通过测试后，再在清理阶段删除不再使用的旧模块。

建议迁移顺序：

1. 添加 V2 包结构和 runtime/storage schema。
2. 添加策略注册表、策略组和策略解析器。
3. 添加行情存储布局和同步流程。
4. 添加信号模型和 artifact writer。
5. 添加使用 V2 信号输出的选股 runner。
6. 添加消费 V2 `SignalSet` 的回测 runner。
7. 添加任务系统迁移层，沿用现有 state.json、console.log、取消和进度模型。
8. 添加策略组 API 和前端支持。
9. 将 Web 执行流程切换到 V2 services。
10. 移除旧运行时假设、根目录 `db/` 使用和旧 artifact 格式依赖。
11. 更新 README、Docker、二进制发布包和部署脚本以适配 V2 存储布局。

## 验收标准

- 全新 clone 可以在没有旧运行数据的情况下初始化 V2 storage。
- 旧运行数据清空只能由显式 reset/init 命令触发，普通服务启动不会删除数据。
- 系统可以同步行情到 `storage/market/`，并保留限流、重试、增量同步、16:00 cutoff 和板块过滤能力。
- 系统创建不可删除的 `default` 策略组，且包含所有已注册策略。
- 用户可以创建、重命名、禁用和删除非 default 策略组。
- 用户可以增删策略组成员，并调整策略组和组内成员顺序。
- 用户可以查看全部策略定义，并启用/禁用策略或调整策略默认参数。
- 旧 `configs.json` 的 active selector 可以按映射表导入为 V2 策略设置和 default 组成员。
- 选股可以按 default 组、指定组、指定策略，以及“组 + 策略”的混合请求运行。
- 选股回测支持同样的策略组和策略选择语义。
- 选股写入 V2 `manifest.json`、`lineage.json`、`signals.json`、`picks.parquet`、`summary.json`。
- 回测消费 V2 `SignalSet`，并写入 V2 回测 artifact。
- 回测结果与来源选股执行建立链接。
- 结果页面可以按策略组和策略展示摘要。
- 代码不再依赖根目录 `db/`、旧 `storage/app.db` 或旧 artifact 布局。
- 当前交易规则行为在迁移过程中由测试保护。
- Docker、部署脚本和二进制包初始化流程不再依赖根目录 `db/`。
- 失败和取消任务保留可读日志，并且不注册不完整业务 artifact。
