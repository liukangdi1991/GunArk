# TrendRadar V2 清空重建设计

## 目标

TrendRadar V2 是对当前私人量化研究工作台的一次破坏性重构。目标是把现有混合在一起的模块结构，重建成边界清晰的领域架构：行情数据、策略管理、信号生成、回测、存储、任务系统、API 与报告展示各自有明确职责。

本系统可以在重构期间暂停使用。历史运行数据不重要，不做迁移。

## 已确认决策

- V2 不兼容旧选股结果、旧回测结果、旧 `storage/app.db`、旧 artifact、旧 `db/` 行情 parquet 文件。
- V2 初始化时会清空旧运行数据，并创建新的运行目录结构。
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
- `default` 不能删除。
- 删除非 default 组不会删除策略定义。
- 删除非 default 组时，其组内策略仍保留在其他组里；如果某个策略只属于被删除的组，则自动移回 `default`。
- 一个策略可以属于多个组。
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
- 最终顺序必须稳定：先按组排序，再按组内成员排序，最后用策略显示名打破并列。

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

选股和回测代码不得直接扫描原始 parquet 文件。

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

`execution_links` 记录执行之间的关系，例如：

```text
selection_execution -> backtest_execution
market_sync_execution -> selection_execution
```

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

## 破坏性重建初始化

V2 初始化会删除旧运行数据，并创建新的 V2 运行目录结构。

初始化流程：

1. 确保 `storage/` 存在。
2. 初始化全新的 V2 `storage/app.db`。
3. 创建 `storage/market/`、`storage/objects/`、`storage/objects/jobs/`。
4. 从代码注册可用策略。
5. 创建 `default` 策略组。
6. 将每个已注册策略加入 `default`。
7. 选股或回测运行前必须先完成行情同步。

V2 首版需要在 README 和部署说明中明确写出这次重构的破坏性。

## 测试策略

每个核心切片都应该先写测试，再实现。

必要测试范围：

- 策略组创建、删除和 default 组保护。
- 策略解析器覆盖按组、按策略、混合、空请求、禁用组、禁用策略。
- MarketDataStore 能读取 V2 `storage/market/bars/` 布局。
- `SignalSet` 序列化与反序列化。
- 选股写入 V2 选股 artifact。
- 选股回测使用和选股相同的策略解析器。
- 回测消费 `SignalSet`，不消费旧信号文件。
- 回测交易规则保护当前涨跌停、止损、持仓规则行为。
- `execution_links` 能连接选股和回测结果。
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
7. 添加策略组 API 和前端支持。
8. 将 Web 执行流程切换到 V2 services。
9. 移除旧运行时假设、根目录 `db/` 使用和旧 artifact 格式依赖。
10. 更新 README 和部署脚本以适配 V2 存储布局。

## 验收标准

- 全新 clone 可以在没有旧运行数据的情况下初始化 V2 storage。
- 系统可以同步行情到 `storage/market/`。
- 系统创建不可删除的 `default` 策略组，且包含所有已注册策略。
- 用户可以创建、重命名、禁用和删除非 default 策略组。
- 用户可以增删策略组成员。
- 选股可以按 default 组、指定组、指定策略，以及“组 + 策略”的混合请求运行。
- 选股回测支持同样的策略组和策略选择语义。
- 选股写入 V2 `manifest.json`、`lineage.json`、`signals.json`、`picks.parquet`、`summary.json`。
- 回测消费 V2 `SignalSet`，并写入 V2 回测 artifact。
- 回测结果与来源选股执行建立链接。
- 结果页面可以按策略组和策略展示摘要。
- 代码不再依赖根目录 `db/`、旧 `storage/app.db` 或旧 artifact 布局。
- 当前交易规则行为在迁移过程中由测试保护。
