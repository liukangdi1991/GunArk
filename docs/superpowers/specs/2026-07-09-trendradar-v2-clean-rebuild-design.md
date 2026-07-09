# TrendRadar V2 Clean Rebuild Design

## Purpose

TrendRadar V2 is a destructive rebuild of the current private quantitative research workbench. The goal is to replace the current mixed module structure with explicit domain boundaries for market data, strategy management, signal generation, backtesting, storage, jobs, and API/reporting.

The system is allowed to be unavailable during the rebuild. Historical runtime data is not important and will not be migrated.

## Decisions

- V2 does not support legacy selection results, legacy backtest results, legacy `storage/app.db`, legacy artifacts, or legacy `db/` market parquet files.
- Existing runtime data will be cleared when V2 is initialized.
- Existing strategy algorithms, indicator formulas, and backtest trade-rule behavior may be migrated into the new architecture.
- The Web workbench remains the primary interface, using FastAPI and React unless a later design explicitly changes it.
- All mutable runtime data moves under `storage/`.
- Strategy groups are first-class runtime configuration, not only a front-end display grouping.
- Selection, batch selection, and selection-backtest all resolve strategies through the same strategy resolver.
- Backtests consume standard `SignalSet` artifacts rather than selection-service-specific output structures.

## Non-Goals

- No compatibility adapter for old selection or backtest artifacts.
- No migration of previous execution history.
- No migration of old local market parquet files.
- No multi-user permission model.
- No microservice split.
- No Redis/Celery/PostgreSQL requirement for V2 initial delivery.
- No strategy algorithm redesign during the architecture rebuild.
- No front-end redesign beyond what is required to support the new strategy group and execution model.

## Target Architecture

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

### Layer Responsibilities

`domain/` contains business models, protocols, and deterministic rules. It should not depend on FastAPI, SQLite, or concrete filesystem paths.

`infrastructure/` contains adapters for SQLite, parquet files, Tushare, runtime paths, checksums, and artifact storage.

`app/` contains use-case orchestration: submit a market sync job, run selection, run selection-backtest, cancel a job, and assemble result metadata.

`interfaces/` contains FastAPI routes, request/response schemas, and report view models used by the front end.

## Runtime Data Layout

All mutable runtime data is stored under `storage/`.

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

The root-level `db/` directory is not used by V2. Initial market data is pulled again through the V2 market sync flow.

## Core Domain Flow

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

## Strategy Groups

Strategy groups are editable runtime objects stored in SQLite.

### Data Model

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

### Rules

- The system initializes a `default` group when storage is empty.
- Existing migrated strategies are added to `default`.
- `default` cannot be deleted.
- Deleting a non-default group does not delete strategy definitions.
- When a non-default group is deleted, its member strategies remain available in other groups. If a deleted group was a strategy's only group, that strategy is moved to `default`.
- A strategy can belong to multiple groups.
- Disabled groups are ignored when resolving a group-based run.
- Disabled strategies are ignored in both group-based and direct strategy-based runs.
- Strategy IDs are stable machine identifiers and must not depend on Chinese display names.

### Strategy Registry

Strategy definitions come from code, not from dynamic database-loaded class names.

```text
domain/strategy/
  models.py
  protocol.py
  registry.py
  resolver.py
  adapter.py
  selectors/
```

`registry.py` registers all available strategies.

`resolver.py` turns requested group IDs and strategy IDs into the final ordered strategy list.

`adapter.py` wraps existing selector implementations so their current algorithmic behavior can be reused while V2 introduces a stable strategy protocol.

### Selection Semantics

Selection, batch selection, and selection-backtest requests accept:

```json
{
  "groups": ["default", "trend"],
  "strategies": ["peak_kdj"]
}
```

Resolution rules:

- If `groups` and `strategies` are both empty, use enabled strategies from `default`.
- If only `groups` is provided, run enabled strategies in those enabled groups.
- If only `strategies` is provided, run those enabled strategies directly.
- If both are provided, run the union of enabled group members and enabled direct strategies.
- Duplicates are removed by `strategy_id`.
- Final order is deterministic: group order, member order, then strategy display name for ties.

## Market Data

V2 uses a single market data store abstraction.

```text
domain/market/
  models.py
  data_store.py
  calendar.py
  stock_meta.py
```

`MarketDataStore` provides:

```text
load_bars(codes, start, end, columns)
latest_trade_date()
trading_dates(start, end)
stock_meta(codes)
```

The market data layer owns:

- Parquet file schema.
- Stock code normalization.
- Trading calendar access.
- Latest trade date calculation.
- Missing market data semantics.
- Stock metadata lookup.

Selection and backtest code must not scan raw parquet files directly.

## Signal Model

`SignalSet` is the contract between selection and backtest.

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

Selection writes one standard signal artifact regardless of whether the run is single-day or batch.

## Selection Artifact Schema

```text
storage/objects/executions/<execution_key>/
  manifest.json
  lineage.json
  selection/
    signals.json
    picks.parquet
    summary.json
```

`manifest.json` identifies the execution:

```json
{
  "schema_version": "2.0",
  "execution_key": "20260709_120000_selection",
  "execution_type": "selection",
  "created_at": "2026-07-09T12:00:00",
  "status": "success"
}
```

`lineage.json` records the request and resolved inputs:

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

`signals.json` is the machine-readable backtest input. `picks.parquet` is optimized for result display. `summary.json` contains per-strategy and per-group counts and elapsed time.

## Backtest Model

The backtest layer is split into orchestration and deterministic simulation components.

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

Responsibilities:

- `runner.py` orchestrates one backtest using a `SignalSet`, market data, and a `BacktestSpec`.
- `engine.py` advances trading dates and coordinates entries, exits, portfolio valuation, and skip records.
- `execution.py` owns fill price, fees, slippage, limit-up, and limit-down behavior.
- `portfolio.py` owns cash, positions, sizing, and re-entry rules.
- `metrics.py` computes summary metrics from equity and trades.

Initial V2 must preserve the current trade-rule behavior:

- Signal date is `T`.
- Buy attempt is `T+1 open`.
- Fixed holding period remains equivalent to the current implementation.
- Long-term bull-bear stop and 10-day-low stop remain supported.
- Limit-up buy rejection and limit-down sell rejection remain supported.
- Unlimited-cash and realistic cash modes remain supported if both exist at the time of migration.

## Backtest Artifact Schema

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

Backtest lineage records:

- Source selection execution key.
- Requested groups and strategies.
- Resolved strategy snapshots.
- Signal execution key.
- Market data date range.
- Trade strategy.
- Capital mode.
- Cash per trade.
- Backtest config snapshot.

## SQLite Schema

V2 storage starts from a new schema.

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

`execution_links` records relationships such as:

```text
selection_execution -> backtest_execution
market_sync_execution -> selection_execution
```

## Job System

V2 may remain single-process and thread-based initially, but job state should be isolated behind an app-level interface.

```text
app/jobs/
  executor.py
  state.py
  logs.py
```

The job layer owns:

- Queueing.
- Running.
- Cancelling.
- Progress updates.
- Console logs.
- Linking a job to a produced execution result.

This keeps FastAPI routes from directly managing worker threads and filesystem state.

## API and Front End

FastAPI and React remain the workbench interface.

Required strategy group API capabilities:

```text
GET    /api/strategy-groups
POST   /api/strategy-groups
PATCH  /api/strategy-groups/{group_id}
DELETE /api/strategy-groups/{group_id}
POST   /api/strategy-groups/{group_id}/members
DELETE /api/strategy-groups/{group_id}/members/{strategy_id}
```

Required run capabilities:

```text
POST /api/executions
```

Execution payloads include `groups` and `strategies` for selection, batch selection, and selection-backtest.

The front end should support:

- Strategy group management.
- Grouped strategy selection.
- Running by group.
- Running by one or more individual strategies.
- Running by a group-plus-strategy union.
- Result summaries grouped by strategy group and strategy.

## Destructive Rebuild Initialization

V2 initialization may remove or ignore old runtime data.

The initialization flow:

1. Ensure `storage/` exists.
2. Initialize a fresh V2 `storage/app.db`.
3. Create `storage/market/`, `storage/objects/`, and `storage/objects/jobs/`.
4. Register available strategies from code.
5. Create the `default` strategy group.
6. Add every registered strategy to `default`.
7. Require market sync before selection or backtest can run.

The first V2 release should document the destructive nature of the rebuild clearly in README and deployment notes.

## Testing Strategy

Tests should be written before implementation for each core slice.

Required test areas:

- Strategy group creation, deletion, and default group protection.
- Strategy resolver for group-only, strategy-only, mixed, empty, disabled group, and disabled strategy cases.
- Market data store reads from the V2 `storage/market/bars/` layout.
- SignalSet serialization and deserialization.
- Selection writes V2 selection artifacts.
- Selection-backtest uses the same resolver as selection.
- Backtest consumes `SignalSet`, not legacy signal files.
- Backtest trade rules preserve current behavior for limit price checks, stops, and holding rules.
- Execution links connect selection and backtest results.
- Fresh initialization creates default storage, default group, and strategy memberships.

## Migration Plan

There is no data migration from legacy runtime data.

Implementation may temporarily keep old modules in the repository while V2 modules are built. Once equivalent V2 paths are wired into API and tests, unused old modules can be deleted in a cleanup phase.

The recommended migration order:

1. Add V2 package structure and runtime/storage schema.
2. Add strategy registry, strategy groups, and resolver.
3. Add market storage layout and sync flow.
4. Add signal model and artifact writer.
5. Add selection runner using V2 signal output.
6. Add backtest runner consuming V2 `SignalSet`.
7. Add API endpoints and front-end support for strategy groups.
8. Switch Web execution flow to V2 services.
9. Remove legacy runtime assumptions, root `db/` usage, and old artifact format dependencies.
10. Update README and deployment scripts for V2 storage layout.

## Acceptance Criteria

- A fresh clone can initialize V2 storage without old runtime data.
- The system can sync market data into `storage/market/`.
- The system creates a non-deletable `default` strategy group containing all registered strategies.
- Users can create, rename, disable, and delete non-default strategy groups.
- Users can add and remove strategy memberships from groups.
- Selection can run by default group, by selected groups, by selected strategies, and by a mixed group-plus-strategy request.
- Selection-backtest supports the same group and strategy selection semantics.
- Selection writes V2 `manifest.json`, `lineage.json`, `signals.json`, `picks.parquet`, and `summary.json`.
- Backtest consumes V2 `SignalSet` and writes V2 backtest artifacts.
- Backtest results are linked to their source selection execution.
- Result pages can show summaries grouped by strategy group and strategy.
- The code no longer requires root-level `db/`, old `storage/app.db`, or old artifact layouts.
- Current trade-rule behavior is protected by tests during migration.
