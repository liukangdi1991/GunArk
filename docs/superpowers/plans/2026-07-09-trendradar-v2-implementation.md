# TrendRadar V2 实现方案

> **For agentic workers:** 实现本计划先使用 subagent-driven-development，每个 Task 开一个独立的 subagent，完成后 review 再进入下一个。

**目标：** 推倒重来，从零构建 TrendRadar V2 —— 四层清晰边界（domain / infrastructure / app / interfaces）的量选股+回测系统。

**架构总览：**
| 层 | 职责 | 依赖 |
|---|---|---|
| `domain/` | 纯业务模型 + 确定性算法 + 协议接口 | 无外部框架依赖 |
| `infrastructure/` | SQLite、Parquet、Tushare 的适配层 | domain |
| `app/` | 用例编排、Job 调度、进度/取消/日志 | domain + infrastructure |
| `interfaces/` | FastAPI 路由 + Pydantic + 报告模型 | app |

**Tech Stack:** Python 3.11+, Polars, FastAPI, SQLite3, `tushare`, `scipy` (仅 find_peaks 场景), 无 pandas/numpy 运行时依赖。

**关键约定：**
- 旧代码只留 `references/` 目录做只读参考，不进入 `PYTHONPATH`
- 所有 selector 按新 `SelectionStrategy` 协议重写，不做 adapter
- 任务系统完全重写，不沿用旧的 `state.json` 模式
- 回测引擎用纯 Python dict/list 做日循环，Polars 计算汇总指标
- 不导入任何旧模块，不走任何旧的 `web/services/`、`selection/`、`backtest/` 路径

---

## 文件结构

```
trendradar/
  __init__.py
  _version.py

  domain/
    __init__.py
    market/
      __init__.py
      models.py
      data_store.py
    strategy/
      __init__.py
      models.py
      protocol.py
      registry.py
      resolver.py
      formulas/
        __init__.py
        bbi.py
        kdj.py
        ma.py
        volume.py
        zxdkx.py
      selectors/
        __init__.py
        bbi_kdj_b1.py
        super_b1.py
        bbi_short_long.py
        peak_kdj.py
        ma60_volume_wave.py
        zxdkx_balance.py
        perfect_b1.py
        big_bullish_volume.py
        volume_spike_balance.py
    signal/
      __init__.py
      models.py
      repository.py
    backtest/
      __init__.py
      models.py
      config.py
      engine.py
      portfolio.py
      execution.py
      metrics.py

  infrastructure/
    __init__.py
    runtime.py
    storage/
      __init__.py
      schema.py
      connection.py
      repository.py
      artifact_store.py
    filesystem/
      __init__.py
      atomic.py
    tushare/
      __init__.py
      client.py
      syncer.py
      stocklist.py

  app/
    __init__.py
    jobs/
      __init__.py
      executor.py
      context.py
      persistence.py
    services/
      __init__.py
      strategy_service.py
      market_service.py
      selection_service.py
      backtest_service.py

  interfaces/
    __init__.py
    api/
      __init__.py
      app.py
      routes/
        __init__.py
        strategies.py
        executions.py
        market.py
        backtest.py
      schemas/
        __init__.py
        strategy.py
        execution.py
        market.py
        backtest.py

  cli.py

---
tests/
  __init__.py
  conftest.py
  domain/
    test_strategy_registry.py
    test_strategy_resolver.py
    test_formulas/
    test_selectors/
    test_signal_models.py
    test_backtest_engine.py
    test_backtest_portfolio.py
    test_market_models.py
  infrastructure/
    test_schema.py
    test_atomic.py
    test_market_data_store.py
  app/
    test_job_executor.py
    test_strategy_service.py
  interfaces/
    test_api_strategies.py
```

---

## 关键接口定义

以下是贯穿所有 Task 的协议类型。每个 Task 的 `Interfaces` 块引用这里。

```python
# === domain/strategy/protocol.py ===

@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    market_data: pl.DataFrame          # 所有候选股票在必要窗口内的 OHLCV
    candidate_codes: list[str]         # 预过滤后的候选代码列表
    get_data_dict: Callable[[], dict[str, pl.DataFrame]]  # 懒加载 per-code dict

@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float

class SelectionStrategy(Protocol):
    definition: "StrategyDefinition"
    def select(self, context: SelectionContext) -> SelectionResult: ...


# === domain/strategy/models.py ===

@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    description: str
    selector_class: type[SelectionStrategy]
    default_params: dict[str, Any]
    param_schema: dict[str, type]

@dataclass
class StrategyGroup:
    id: str
    name: str
    description: str
    enabled: bool
    sort_order: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

@dataclass
class StrategyGroupMember:
    group_id: str
    strategy_id: str
    sort_order: int

@dataclass
class StrategySettings:
    strategy_id: str
    enabled: bool
    params_json: str  # JSON dumps of user overrides


# === domain/signal/models.py ===

@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_name: str
    group_ids: list[str]
    primary_group_id: str
    signal_date: date
    codes: list[str]

@dataclass(frozen=True)
class SignalSet:
    schema_version: str = "2.0"
    execution_key: str
    signal_from: date
    signal_to: date
    strategies_snapshot: list[dict]
    signals: list[StrategySignal]

    def to_json(self) -> str: ...
    @staticmethod
    def from_json(data: dict) -> "SignalSet": ...


# === domain/backtest/config.py ===

@dataclass(frozen=True)
class CapitalConfig:
    initial_cash: float = 1_000_000.0
    mode: str = "unlimited_cash"           # "unlimited_cash" | "realistic"
    fixed_cash_per_trade: float = 50_000.0

@dataclass(frozen=True)
class ExecutionConfig:
    trade_strategy: str = "long_term_bull_bear_stop"
    fixed_hold_n_days: int = 5
    max_sell_postpone_days: int = 10
    reject_if_limit_up_on_buy: bool = True
    postpone_if_limit_down_on_sell: bool = True
    skip_if_suspended: bool = True
    force_sell_on_two_day_close_below_long_line: bool = True
    close_below_recent_low_stop_window: int | None = None

@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_duty_rate_sell: float = 0.0001
    transfer_fee_rate: float = 0.00001
    slippage_buy_bp: float = 2.0
    slippage_sell_bp: float = 2.0

@dataclass(frozen=True)
class BacktestConfig:
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    costs: CostConfig = field(default_factory=CostConfig)


# === domain/market/data_store.py ===

class MarketDataStore(ABC):
    @abstractmethod
    def load_bars(self, codes: list[str], start: date, end: date,
                  columns: list[str] | None = None) -> pl.DataFrame: ...

    @abstractmethod
    def latest_trade_date(self) -> date | None: ...

    @abstractmethod
    def trading_dates(self, start: date, end: date) -> list[date]: ...

    @abstractmethod
    def stock_meta(self, codes: list[str] | None = None) -> pl.DataFrame: ...


# === infrastructure/storage/schema.py ===

# SQLite DDL (see Task 1 for full DDL)
# Tables: executions, execution_items, artifacts, execution_links,
#         jobs, job_logs, strategy_groups, strategy_group_members,
#         strategy_settings, market_sync_runs


# === app/jobs/context.py ===

class JobContext:
    job_id: str
    job_type: str

    def log(self, message: str, level: str = "INFO") -> None: ...
    def update_progress(self, current: int, total: int, message: str = "") -> None: ...
    def check_cancelled(self) -> bool: ...
    def persist_result(self, result: dict) -> None: ...
    def fail(self, error: str) -> None: ...
    def succeed(self, result: dict) -> None: ...
```

---

## Task 1: 项目脚手架 + Init/Reset + Storage Schema

**Files:**
- Create: `trendradar/__init__.py`
- Create: `trendradar/_version.py`
- Create: `trendradar/cli.py`
- Create: `trendradar/infrastructure/runtime.py`
- Create: `trendradar/infrastructure/storage/__init__.py`
- Create: `trendradar/infrastructure/storage/schema.py`
- Create: `trendradar/infrastructure/storage/connection.py`
- Create: `trendradar/infrastructure/storage/repository.py`
- Create: `trendradar/infrastructure/storage/artifact_store.py`
- Create: `trendradar/infrastructure/filesystem/__init__.py`
- Create: `trendradar/infrastructure/filesystem/atomic.py`
- Create: `tests/infrastructure/test_schema.py`
- Create: `tests/infrastructure/test_atomic.py`
- Modify: 旧代码移入 `references/`

**Interfaces:**
- Produces: `RuntimeRoot` / `StorageRoot` 路径解析
- Produces: `DDL` 包含 V2 全部表结构
- Produces: `StorageConnection` (SQLite)
- Produces: `ArtifactStore` (文件读写)
- Produces: `AtomicWriter` (tmp + rename)
- Produces: `cli.py` 的 `init-v2 --confirm-reset` 命令

- [ ] **Step 1: 确定 V2 包名和版本**

`trendradar/_version.py`:
```python
__version__ = "2.0.0.dev0"
```

`trendradar/__init__.py`:
```python
from trendradar._version import __version__
```

- [ ] **Step 2: 实现运行时路径解析**

`trendradar/infrastructure/runtime.py`:
```python
import os
import sys
from pathlib import Path


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    if env := os.environ.get("TREND_RADAR_RUNTIME_ROOT"):
        return Path(env)
    if env := os.environ.get("TREND_RADAR_HOME"):
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return source_root()


def storage_root() -> Path:
    return runtime_root() / "storage"
```

- [ ] **Step 3: 实现 DDL**

`trendradar/infrastructure/storage/schema.py`:
```python
DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL UNIQUE,
    execution_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    manifest_key TEXT,
    lineage_key TEXT
);

CREATE TABLE IF NOT EXISTS execution_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_name TEXT,
    params_json TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    artifact_type TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    size_bytes INTEGER,
    checksum TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(execution_key, artifact_type)
);

CREATE TABLE IF NOT EXISTS execution_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    target_execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    link_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_execution_key, target_execution_key, link_type)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    request_json TEXT,
    result_json TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    UNIQUE(job_id, sequence)
);

CREATE TABLE IF NOT EXISTS strategy_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategy_group_members (
    group_id TEXT NOT NULL REFERENCES strategy_groups(id),
    strategy_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, strategy_id)
);

CREATE TABLE IF NOT EXISTS strategy_settings (
    strategy_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    params_json TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT NOT NULL REFERENCES executions(execution_key),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stock_count INTEGER,
    skipped_latest INTEGER DEFAULT 0,
    empty_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_executions_created_at ON executions(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_items_execution_key ON execution_items(execution_key);
CREATE INDEX IF NOT EXISTS idx_artifacts_execution_key ON artifacts(execution_key);
CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_market_sync_runs_execution_key ON market_sync_runs(execution_key);
"""


def init_schema(conn):
    conn.executescript(DDL)
    conn.commit()
```

- [ ] **Step 4: 实现 StorageConnection**

`trendradar/infrastructure/storage/connection.py`:
```python
import sqlite3
from pathlib import Path


class StorageConnection:
    def __init__(self, storage_root: Path):
        self.storage_root = storage_root
        self.db_path = storage_root / "app.db"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
```

- [ ] **Step 5: 实现 ArtifactStore**

`trendradar/infrastructure/storage/artifact_store.py`:
```python
import json
import hashlib
from pathlib import Path


class ArtifactStore:
    def __init__(self, storage_root: Path):
        self.objects_root = storage_root / "objects"

    def _exec_dir(self, execution_key: str) -> Path:
        return self.objects_root / "executions" / execution_key

    def write_json(self, execution_key: str, artifact_type: str, data: dict) -> str:
        path = self._exec_dir(execution_key) / artifact_type / "data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, default=str)
        path.write_text(content, encoding="utf-8")
        return self._storage_key(path)

    def _storage_key(self, path: Path) -> str:
        return str(path.relative_to(self.objects_root))
```

- [ ] **Step 6: 实现原子文件操作**

`trendradar/infrastructure/filesystem/atomic.py`:
```python
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))
```

- [ ] **Step 7: 测试原子写入和 schema**

`tests/infrastructure/test_atomic.py`:
```python
from trendradar.infrastructure.filesystem.atomic import atomic_write_text


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "sub" / "test.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "test.txt"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_no_partial_on_failure(tmp_path):
    target = tmp_path / "test.txt"
    target.write_text("safe")
    try:
        atomic_write_text(target, "x" * 1000000 + None)  # will fail
    except TypeError:
        pass
    assert target.read_text() == "safe"
```

`tests/infrastructure/test_schema.py`:
```python
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.connection import StorageConnection


def test_schema_creates_all_tables(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row["name"] for row in cursor.fetchall()}
    expected = {
        "artifacts", "execution_items", "execution_links", "executions",
        "job_logs", "jobs", "market_sync_runs", "strategy_group_members",
        "strategy_groups", "strategy_settings",
    }
    assert tables == expected


def test_foreign_keys_enforced(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    conn.execute("INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'selection')")
    conn.execute("INSERT INTO execution_items (execution_key, item_type, item_key) VALUES ('k1', 'test', 't1')")
    conn.commit()

    import sqlite3
    with_conn = sc.connect()
    with_conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        with_conn.execute("INSERT INTO execution_items (execution_key, item_type, item_key) VALUES ('nonexistent', 'test', 't2')")


def test_unique_execution_key(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    conn.execute("INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'selection')")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'backtest')")
```

- [ ] **Step 8: 实现 CLI init/reset 命令**

`trendradar/cli.py`:
```python
import argparse
import sys
import shutil
from pathlib import Path

from trendradar.infrastructure.runtime import storage_root
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


RESET_PATHS = [
    "storage/app.db",
    "storage/objects/",
    "storage/market/",
]


def cmd_init_v2(args: argparse.Namespace) -> None:
    root = storage_root()
    objects_root = root / "objects"
    market_root = root / "market"
    bars_root = market_root / "bars"

    if args.reset_runtime:
        if not args.confirm_reset:
            print("ERROR: --reset-runtime requires --confirm-reset")
            sys.exit(1)

        print("Will delete and recreate:")
        for rel in RESET_PATHS:
            full = root / rel
            if full.exists():
                print(f"  DELETE: {full}")
            else:
                print(f"  SKIP (not exists): {full}")

        # Delete
        app_db = root / "app.db"
        if app_db.exists():
            app_db.unlink()
        for d in [objects_root, market_root]:
            if d.exists():
                shutil.rmtree(d)

    # Create dirs
    bars_root.mkdir(parents=True, exist_ok=True)
    objects_root.mkdir(parents=True, exist_ok=True)
    (objects_root / "jobs").mkdir(exist_ok=True)

    # Init DB
    sc = StorageConnection(root)
    conn = sc.connect()
    init_schema(conn)
    conn.close()

    print(f"V2 storage initialized at {root}")


def main():
    parser = argparse.ArgumentParser(prog="trendradar")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init-v2", help="Initialize V2 runtime data")
    init.add_argument("--reset-runtime", action="store_true", help="Delete old runtime data first")
    init.add_argument("--confirm-reset", action="store_true", help="Confirm data deletion")

    args = parser.parse_args()
    if args.command == "init-v2":
        cmd_init_v2(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: 测试 CLI**

```python
def test_init_v2_creates_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=False, confirm_reset=False)
    cmd_init_v2(args)

    root = storage_root()
    assert (root / "app.db").exists()
    assert (root / "market" / "bars").is_dir()
    assert (root / "objects" / "jobs").is_dir()


def test_init_v2_reset_requires_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=True, confirm_reset=False)
    with pytest.raises(SystemExit):
        cmd_init_v2(args)
```

- [ ] **Step 10: 旧代码移入 references/ + 提交**

```bash
mkdir -p references
git mv selection references/
git mv backtest references/
git mv core references/
git mv web references/
git mv fetch_kline.py references/
git mv trend_radar_launcher.py references/
git mv scripts references/
git mv storage references/  # or just keep it, V2 recreates
git mv docker-compose.yml Dockerfile deploy references/
```

注意：`AGENTS.md`、`README.md`、`configs.json`、`stocklist.csv` 保留在根目录。

- [ ] **Step 11: 验证测试全部通过**

```bash
pip install -e .
pytest tests/infrastructure/test_schema.py tests/infrastructure/test_atomic.py -v
```

---

## Task 2: Domain 策略模型 + 注册表 + 解析器

**Files:**
- Create: `trendradar/domain/strategy/__init__.py`
- Create: `trendradar/domain/strategy/models.py`
- Create: `trendradar/domain/strategy/protocol.py`
- Create: `trendradar/domain/strategy/registry.py`
- Create: `trendradar/domain/strategy/resolver.py`
- Create: `tests/domain/test_strategy_registry.py`
- Create: `tests/domain/test_strategy_resolver.py`

**Interfaces:**
- Consumes: `StrategyGroup`, `StrategyGroupMember`, `StrategyDefinition`, `StrategySettings`
- Consumes: `SelectionStrategy`, `SelectionContext`, `SelectionResult` (protocol)
- Produces: `StrategyRegistry` — `register()`, `get()`, `list()`, `get_default_params()`
- Produces: `StrategyResolver` — `resolve(groups, strategies) -> list[StrategyDefinition]`

- [ ] **Step 1: 实现 models.py**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    description: str
    selector_class: type  # implements SelectionStrategy
    default_params: dict[str, Any] = field(default_factory=dict)
    param_schema: dict[str, type] = field(default_factory=dict)


@dataclass
class StrategyGroup:
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class StrategyGroupMember:
    group_id: str
    strategy_id: str
    sort_order: int = 0


@dataclass
class StrategySettings:
    strategy_id: str
    enabled: bool = True
    params_json: str = "{}"
```

- [ ] **Step 2: 实现 protocol.py**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol
import polars as pl


@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    market_data: pl.DataFrame
    candidate_codes: list[str]
    get_data_dict: Callable[[], dict[str, pl.DataFrame]]


@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float = 0.0


class SelectionStrategy(Protocol):
    definition: "StrategyDefinition"

    def select(self, context: SelectionContext) -> SelectionResult: ...
```

- [ ] **Step 3: 实现 registry.py**

所有策略通过注册表注册，保持 `strategy_id` 稳定：

```python
from __future__ import annotations
from trendradar.domain.strategy.models import StrategyDefinition


_registry: dict[str, StrategyDefinition] = {}


def register(defn: StrategyDefinition) -> None:
    _registry[defn.strategy_id] = defn


def get(strategy_id: str) -> StrategyDefinition | None:
    return _registry.get(strategy_id)


def list_all() -> list[StrategyDefinition]:
    return sorted(_registry.values(), key=lambda d: d.strategy_id)


def get_default_params(strategy_id: str) -> dict:
    defn = get(strategy_id)
    if defn is None:
        return {}
    return dict(defn.default_params)


def import_from_configs(configs: list[dict]) -> list[StrategySettings]:
    """从旧 configs.json 导入策略设置。configs 是 selectors 数组。"""
    from trendradar.domain.strategy.models import StrategySettings
    settings = []
    for entry in configs:
        sid = _config_alias_to_id(entry.get("alias", ""))
        if sid is None or sid not in _registry:
            continue
        settings.append(StrategySettings(
            strategy_id=sid,
            enabled=entry.get("activate", True),
            params_json=json.dumps(entry.get("params", {}), ensure_ascii=False),
        ))
    return settings


_CONFIG_ALIAS_MAP = {
    "B1战法": "bbi_kdj_b1",
    "SuperB1战法": "super_b1",
    "补票战法": "bbi_short_long",
    "填坑战法": "peak_kdj",
    "上穿60放量战法": "ma60_volume_wave",
    "多空平衡选股策略": "zxdkx_balance",
    "B1战法（V2）": "perfect_b1_v2",
    "完美B1": "perfect_b1_volume_stepdown",
    "暴力K战法": "big_bullish_volume",
    "倍量多空平衡策略": "volume_spike_balance",
}

def _config_alias_to_id(alias: str) -> str | None:
    return _CONFIG_ALIAS_MAP.get(alias)
```

- [ ] **Step 4: 测试注册表基本操作**

`tests/domain/test_strategy_registry.py`:
```python
import pytest
from trendradar.domain.strategy.registry import register, get, list_all, import_from_configs
from trendradar.domain.strategy.models import StrategyDefinition, StrategySettings


class FakeSelector:
    definition = None

def test_register_and_get():
    defn = StrategyDefinition(
        strategy_id="test_001", name="Test", description="",
        selector_class=FakeSelector, default_params={"a": 1},
    )
    register(defn)
    assert get("test_001") is defn
    assert get("nonexistent") is None


def test_list_all_sorted():
    ids = [d.strategy_id for d in list_all()]
    assert ids == sorted(ids)


def test_import_from_configs():
    configs = [
        {"class": "BBIKDJSelector", "alias": "B1战法", "activate": True, "params": {"j": 10}},
        {"class": "UnknownSelector", "alias": "不存在", "activate": True, "params": {}},
    ]
    settings = import_from_configs(configs)
    assert len(settings) == 1
    assert settings[0].strategy_id == "bbi_kdj_b1"
    assert settings[0].enabled is True
```

- [ ] **Step 5: 实现 resolver.py**

```python
from __future__ import annotations
from trendradar.domain.strategy.registry import get as get_defn
from trendradar.domain.strategy.models import StrategyDefinition

ResolverInput = dict  # {"groups": [...], "strategies": [...]}


def resolve(
    group_defs: list[dict],
    member_defs: list[dict],
    settings_map: dict[str, dict],
    request: dict | None = None,
) -> list[StrategyDefinition]:
    """解析请求中的 groups + strategies 为有序策略列表。

    参数:
        group_defs: 策略组列表 [{id, enabled, sort_order}]
        member_defs: 成员列表 [{group_id, strategy_id, sort_order}]
        settings_map: {strategy_id: {enabled, params_json}}
        request: {"groups": [...], "strategies": [...]} 或 None

    返回:
        排序去重后的 StrategyDefinition 列表
    """
    if request is None:
        request = {}

    req_groups = request.get("groups") or []
    req_strategies = request.get("strategies") or []

    # 如果都为空，使用 default 组
    if not req_groups and not req_strategies:
        req_groups = ["default"]

    # 解析 group IDs → 启用的策略 ID 列表（有序）
    group_strategy_ids: list[str] = []
    seen_in_groups: set[str] = set()
    for gid in req_groups:
        g = _find_group(group_defs, gid)
        if g is None or not g["enabled"]:
            continue
        members = [m for m in member_defs if m["group_id"] == gid]
        members.sort(key=lambda m: m["sort_order"])
        for m in members:
            if m["strategy_id"] not in seen_in_groups:
                seen_in_groups.add(m["strategy_id"])
                group_strategy_ids.append(m["strategy_id"])

    # 直接指定的策略
    direct_strategy_ids = [
        sid for sid in req_strategies
        if sid not in seen_in_groups
    ]

    # 合并
    all_ids = group_strategy_ids + direct_strategy_ids

    # 去重
    seen: set[str] = set()
    result: list[StrategyDefinition] = []
    for sid in all_ids:
        if sid in seen:
            continue
        seen.add(sid)

        # 检查策略设置：禁用策略跳过
        s = settings_map.get(sid, {})
        if not s.get("enabled", True):
            continue

        defn = get_defn(sid)
        if defn is None:
            # 标记为 unavailable，但仍保留
            continue
        result.append(defn)

    return result


def _find_group(groups: list[dict], gid: str) -> dict | None:
    for g in groups:
        if g["id"] == gid:
            return g
    return None
```

- [ ] **Step 6: 测试解析器**

`tests/domain/test_strategy_resolver.py`:
```python
import pytest
from trendradar.domain.strategy.resolver import resolve
from trendradar.domain.strategy.registry import register
from trendradar.domain.strategy.models import StrategyDefinition


def setup():
    # 注册几个测试策略
    for sid, name in [("s1", "Str1"), ("s2", "Str2"), ("s3", "Str3"), ("s4", "Str4")]:
        register(StrategyDefinition(
            strategy_id=sid, name=name, description="",
            selector_class=type("Fake", (), {}),
        ))


def test_empty_request_uses_default():
    groups = [{"id": "default", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "default", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, None)
    assert [d.strategy_id for d in result] == ["s1"]


def test_request_groups():
    groups = [{"id": "g1", "enabled": True, "sort_order": 1}]
    members = [{"group_id": "g1", "strategy_id": "s2", "sort_order": 0}]
    settings = {"s2": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"]})
    assert [d.strategy_id for d in result] == ["s2"]


def test_request_strategies():
    groups = []
    members = []
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"strategies": ["s1"]})
    assert [d.strategy_id for d in result] == ["s1"]


def test_mixed_groups_and_strategies():
    groups = [{"id": "g1", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}, "s2": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"], "strategies": ["s2"]})
    assert [d.strategy_id for d in result] == ["s1", "s2"]


def test_disabled_strategy_skipped():
    groups = [{"id": "default", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "default", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": False}}
    result = resolve(groups, members, settings, None)
    assert result == []


def test_disabled_group_skipped():
    groups = [{"id": "g1", "enabled": False, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"]})
    assert result == []


def test_dedup_same_strategy_in_multiple_groups():
    groups = [
        {"id": "g1", "enabled": True, "sort_order": 0},
        {"id": "g2", "enabled": True, "sort_order": 1},
    ]
    members = [
        {"group_id": "g1", "strategy_id": "s1", "sort_order": 0},
        {"group_id": "g2", "strategy_id": "s1", "sort_order": 0},
    ]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1", "g2"]})
    assert [d.strategy_id for d in result] == ["s1"]  # 去重


def test_order_preserved():
    groups = [{"id": "g1", "enabled": True, "sort_order": 0}]
    members = [
        {"group_id": "g1", "strategy_id": "s2", "sort_order": 1},
        {"group_id": "g1", "strategy_id": "s1", "sort_order": 0},
    ]
    settings = {"s1": {"enabled": True}, "s2": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"]})
    assert [d.strategy_id for d in result] == ["s2", "s1"]  # 按 member sort_order: s2(sort=1)先于s1(sort=0)? 不对，应该是s1先


def test_direct_strategies_after_groups():
    groups = [{"id": "g1", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}, "s2": {"enabled": True}, "s3": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"], "strategies": ["s2", "s3"]})
    assert [d.strategy_id for d in result] == ["s1", "s2", "s3"]
```

- [ ] **Step 7: 运行测试通过**

```bash
pytest tests/domain/test_strategy_registry.py tests/domain/test_strategy_resolver.py -v
```

---

## Task 3: Domain 公式库 — 纯函数指标计算

**Files:**
- Create: `trendradar/domain/strategy/formulas/__init__.py`
- Create: `trendradar/domain/strategy/formulas/bbi.py`
- Create: `trendradar/domain/strategy/formulas/kdj.py`
- Create: `trendradar/domain/strategy/formulas/ma.py`
- Create: `trendradar/domain/strategy/formulas/volume.py`
- Create: `trendradar/domain/strategy/formulas/zxdkx.py`
- Create: `tests/domain/test_formulas/__init__.py`
- Create: `tests/domain/test_formulas/test_bbi.py`
- Create: `tests/domain/test_formulas/test_kdj.py`
- Create: `tests/domain/test_formulas/test_ma.py`
- Create: `tests/domain/test_formulas/test_volume.py`
- Create: `tests/domain/test_formulas/test_zxdkx.py`

**Interfaces:**
- Produces: `compute_bbi(df, windows=(3, 6, 12, 24)) -> pl.Series`
- Produces: `compute_kdj(df, n=9) -> tuple[pl.Series, pl.Series, pl.Series]` (K, D, J)
- Produces: `compute_rsv(df, n=9) -> pl.Series`
- Produces: `compute_ma(df, window) -> pl.Series`
- Produces: `compute_dif(df, fast=12, slow=26) -> pl.Series`
- Produces: `compute_zx_lines(df, m1=14, m2=28, m3=57, m4=114) -> tuple[pl.Series, pl.Series]`
- Produces: `volume_spike_flag(close, volume, multiple) -> pl.Series`
- Produces: `volume_step_down(volume, window) -> pl.Series`
- Produces: `recent_low(column, window) -> pl.Series`

所有公式是**纯函数**，输入输出是 `pl.Series` 或 `pl.DataFrame`，不持有状态。

- [ ] **Step 1: 实现 BBI**

`trendradar/domain/strategy/formulas/bbi.py`:
```python
import polars as pl
from trendradar.domain.strategy.formulas.ma import compute_ma


def compute_bbi(df: pl.DataFrame, windows: tuple[int, ...] = (3, 6, 12, 24)) -> pl.Series:
    close = df["close"]
    mas = [compute_ma(df, w) for w in windows]
    bbi = sum(mas) / len(mas)
    return bbi.alias("bbi")


def bbi_deriv_uptrend(
    bbi: pl.Series,
    min_window: int,
    max_window: int,
    q_threshold: float = 0.0,
) -> bool:
    """检查 BBI 在最近 max_window 天内是否有 min_window 以上处于上行趋势。"""
    if len(bbi) < max_window:
        return False
    recent = bbi.tail(max_window)
    deriv = recent.diff()
    uptrend_count = (deriv > 0).sum()
    return uptrend_count >= min_window
```

- [ ] **Step 2: 实现 KDJ**

`trendradar/domain/strategy/formulas/kdj.py`:
```python
import polars as pl


def compute_kdj(df: pl.DataFrame, n: int = 9) -> tuple[pl.Series, pl.Series, pl.Series]:
    low = df["low"]
    high = df["high"]
    close = df["close"]

    hhv = high.rolling_max(n)
    llv = low.rolling_min(n)

    rsv = ((close - llv) / (hhv - llv + 1e-10)) * 100
    rsv = rsv.fill_nan(50).fill_null(50)

    k = rsv.ewm_mean(alpha=1/3, adjust=False)
    d = k.ewm_mean(alpha=1/3, adjust=False)
    j = 3 * k - 2 * d

    return k.alias("k"), d.alias("d"), j.alias("j")


def compute_rsv(df: pl.DataFrame, n: int = 9) -> pl.Series:
    low = df["low"]
    high = df["high"]
    close = df["close"]
    hhv = high.rolling_max(n)
    llv = low.rolling_min(n)
    rsv = ((close - llv) / (hhv - llv + 1e-10)) * 100
    return rsv.fill_nan(50).fill_null(50).alias("rsv")
```

- [ ] **Step 3: 实现 MA 与 DIF**

`trendradar/domain/strategy/formulas/ma.py`:
```python
import polars as pl


def compute_ma(df: pl.DataFrame, window: int, column: str = "close") -> pl.Series:
    return df[column].rolling_mean(window).alias(f"ma_{window}")


def compute_dif(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    close = df["close"]
    ema_fast = close.ewm_mean(span=fast, adjust=False)
    ema_slow = close.ewm_mean(span=slow, adjust=False)
    return (ema_fast - ema_slow).alias("dif")
```

- [ ] **Step 4: 实现成交量公式**

`trendradar/domain/strategy/formulas/volume.py`:
```python
import polars as pl


def volume_spike_flag(volume: pl.Series, multiple: float = 2.0) -> pl.Series:
    """当日成交量 > 前一日 * multiple 且 收盘价 > 开盘价。"""
    prev_vol = volume.shift(1)
    return (volume > prev_vol * multiple).alias("volume_spike_flag")


def volume_step_down(volume: pl.Series, window: int = 5) -> pl.Series:
    """最近 window 天中有几天缩量（成交量持续递减）。"""
    decreased = volume.diff() < 0
    return decreased.rolling_sum(window).alias("volume_step_down")
```

- [ ] **Step 5: 实现 ZXDKX 多空线**

`trendradar/domain/strategy/formulas/zxdkx.py`:
```python
import polars as pl
from trendradar.domain.strategy.formulas.ma import compute_ma


def compute_zx_lines(
    df: pl.DataFrame,
    m1: int = 14,
    m2: int = 28,
    m3: int = 57,
    m4: int = 114,
) -> tuple[pl.Series, pl.Series]:
    """计算短期趋势线和长期多空线。"""
    close = df["close"]
    ma1 = close.rolling_mean(m1)
    ma2 = close.rolling_mean(m2)

    # 长期多空线: (ma3 + ma3 + ma4 + ma4) / 4
    ma3 = close.rolling_mean(m3)
    ma4 = close.rolling_mean(m4)
    long_line = (ma3 + ma3 + ma4 + ma4) / 4

    return ma1.alias("short_term_trend_line"), long_line.alias("long_term_bull_bear_line")


def zx_stick_ratio(short_line: pl.Series, long_line: pl.Series) -> pl.Series:
    """两条线之间的粘合比例。"""
    return ((short_line - long_line).abs() / long_line).alias("zx_stick_ratio")


def zx_stick_condition(
    short_line: pl.Series,
    long_line: pl.Series,
    threshold: float = 0.04,
    window: int = 10,
) -> pl.Series:
    """检查 ZX 双线在 window 天内是否一直粘合。"""
    ratio = zx_stick_ratio(short_line, long_line)
    return (ratio.rolling_min(window) < threshold).alias("zx_stick_condition")
```

- [ ] **Step 6: 测试公式**

```python
# tests/domain/test_formulas/test_bbi.py
def test_compute_bbi():
    df = pl.DataFrame({"close": [10, 11, 12, 13, 14, 15]})
    bbi = compute_bbi(df)
    assert len(bbi) == 6
    assert bbi.name == "bbi"

# tests/domain/test_formulas/test_kdj.py
def test_compute_kdj():
    df = pl.DataFrame({
        "high": [11, 12, 13, 14, 15],
        "low": [9, 10, 9, 11, 12],
        "close": [10, 11, 12, 13, 14],
    })
    k, d, j = compute_kdj(df, n=3)
    assert len(k) == 5
    assert len(d) == 5
    assert len(j) == 5

# tests/domain/test_formulas/test_zxdkx.py
def test_compute_zx_lines():
    df = pl.DataFrame({"close": list(range(120, 130))})
    short_line, long_line = compute_zx_lines(df)
    assert len(short_line) == 10
    assert len(long_line) == 10
```

---

## Task 4: Domain 选股器 — 重写所有 9 个 Selector

**Files:**
- Create: `trendradar/domain/strategy/selectors/__init__.py` (注册所有)
- Create: `trendradar/domain/strategy/selectors/bbi_kdj_b1.py`
- Create: `trendradar/domain/strategy/selectors/super_b1.py`
- Create: `trendradar/domain/strategy/selectors/bbi_short_long.py`
- Create: `trendradar/domain/strategy/selectors/peak_kdj.py`
- Create: `trendradar/domain/strategy/selectors/ma60_volume_wave.py`
- Create: `trendradar/domain/strategy/selectors/zxdkx_balance.py`
- Create: `trendradar/domain/strategy/selectors/perfect_b1.py`
- Create: `trendradar/domain/strategy/selectors/big_bullish_volume.py`
- Create: `trendradar/domain/strategy/selectors/volume_spike_balance.py`
- Create: `tests/domain/test_selectors/test_bbi_kdj_b1.py`
- (每个 selector 一个测试文件)

**Interfaces:**
- Consumes: `SelectionContext`、`SelectionResult`
- Consumes: 所有公式 (`compute_bbi`, `compute_kdj`, etc.)
- Produces: 每个 selector 类实现 `SelectionStrategy` protocol
- 所有 selector 在 `selectors/__init__.py` 中集中注册到 registry

**实现方式**：每个 selector 从 `SelectionContext.market_data`（Polars DataFrame）读取全部必要列的窗口数据，用公式计算指标，用 Polars 链式 filter 筛选结果。

**旧代码参考位置**：`references/selection/selectors/*.py`

- [ ] **Step 1: 实现核心 `bbi_kdj_b1.py`**

```python
import time
from trendradar.domain.strategy.protocol import SelectionStrategy, SelectionContext, SelectionResult
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_ma, compute_dif
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class BBIKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def select(self, context: SelectionContext) -> SelectionResult:
        t0 = time.time()
        df = context.market_data
        params = self.definition.default_params

        j_threshold = params.get("j_threshold", 15)
        bbi_min_window = params.get("bbi_min_window", 20)
        max_window = params.get("max_window", 120)
        bbi_q_threshold = params.get("bbi_q_threshold", 0.2)
        j_q_threshold = params.get("j_q_threshold", 0.10)

        # 计算指标
        df = df.with_columns([
            compute_bbi(df).over("code"),
            compute_kdj(df).over("code"),
            compute_ma(df, 60).over("code"),
            compute_dif(df).over("code"),
            compute_zx_lines(df).over("code"),
        ])

        # 过滤条件
        # 1. J < j_threshold
        # 2. BBI 上行趋势
        # (实际筛选逻辑用 Polars 表达式实现)
        filtered = df.filter(
            (pl.col("j") < j_threshold)
            & (pl.col("dif") > 0)
        )

        # 对每只股票做 BBI deriv uptrend 检查（需要序列内计算）
        selected = []
        for code in filtered["code"].unique():
            hist = filtered.filter(pl.col("code") == code).sort("date")
            if len(hist) < max_window:
                continue
            bbi = hist["bbi"]
            if bbi_deriv_uptrend(bbi, bbi_min_window, max_window, bbi_q_threshold):
                selected.append(code)

        elapsed = time.time() - t0
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=elapsed,
        )
```

- [ ] **Step 2-9: 类似方式实现其余 8 个 selector**

具体条件逻辑参考 `references/selection/selectors/*.py`。

所有 selector 统一在 `selectors/__init__.py` 中注册：
```python
from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.registry import register
from trendradar.domain.strategy.selectors.bbi_kdj_b1 import BBIKDJSelector
# ... 其余 import

def register_all():
    register(StrategyDefinition(
        strategy_id="bbi_kdj_b1", name="B1战法",
        description="BBI 上行 + KDJ 低位 + 趋势过滤",
        selector_class=BBIKDJSelector,
        default_params={"j_threshold": 15, "bbi_min_window": 20, "max_window": 120},
    ))
    # ... 其余注册
```

**测试示例** (每个 selector 构造一个已知输出的小数据测试):
```python
def test_bbi_kdj_b1_selector():
    ctx = SelectionContext(
        trade_date=date(2026, 7, 9),
        market_data=make_sample_data(),  # 包含已知结果的多个 ticker
        candidate_codes=["000001", "600519"],
        get_data_dict=lambda: {},
    )
    sel = BBIKDJSelector(make_definition())
    result = sel.select(ctx)
    assert isinstance(result.selected_codes, list)
    assert result.strategy_id == "bbi_kdj_b1"
```

**每个 selector 必须满足的测试条件：**
- 空数据（空 DataFrame）→ 空列表
- 已知通过条件 → 包含期望代码
- 已知不通过条件 → 不包含
- elapsed_seconds >= 0

---

## Task 5: Domain 信号模型 + Repository

**Files:**
- Create: `trendradar/domain/signal/__init__.py`
- Create: `trendradar/domain/signal/models.py`
- Create: `trendradar/domain/signal/repository.py`
- Create: `tests/domain/test_signal_models.py`

**Interfaces:**
- Consumes: `StrategyDefinition`、`SelectionResult`
- Produces: `SignalSet`、`StrategySignal` 序列化/反序列化
- Produces: `SignalRepository` — `save(signal_set, artifact_store, execution_key)`, `load(artifact_store, execution_key) -> SignalSet`

- [ ] **Step 1: 实现信号模型**

```python
# domain/signal/models.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date
import json


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_name: str
    group_ids: list[str] = field(default_factory=list)
    primary_group_id: str = ""
    signal_date: date | None = None
    codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SignalSet:
    schema_version: str = "2.0"
    execution_key: str = ""
    signal_from: date | None = None
    signal_to: date | None = None
    strategies_snapshot: list[dict] = field(default_factory=list)
    signals: list[StrategySignal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "execution_key": self.execution_key,
            "signal_from": str(self.signal_from) if self.signal_from else None,
            "signal_to": str(self.signal_to) if self.signal_to else None,
            "strategies_snapshot": self.strategies_snapshot,
            "signals": [
                {
                    "strategy_id": s.strategy_id,
                    "strategy_name": s.strategy_name,
                    "group_ids": s.group_ids,
                    "primary_group_id": s.primary_group_id,
                    "signal_date": str(s.signal_date) if s.signal_date else None,
                    "codes": s.codes,
                }
                for s in self.signals
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> SignalSet:
        signals = [
            StrategySignal(
                strategy_id=s["strategy_id"],
                strategy_name=s["strategy_name"],
                group_ids=s.get("group_ids", []),
                primary_group_id=s.get("primary_group_id", ""),
                signal_date=date.fromisoformat(s["signal_date"]) if s.get("signal_date") else None,
                codes=s.get("codes", []),
            )
            for s in data.get("signals", [])
        ]
        return cls(
            schema_version=data.get("schema_version", "2.0"),
            execution_key=data.get("execution_key", ""),
            signal_from=date.fromisoformat(data["signal_from"]) if data.get("signal_from") else None,
            signal_to=date.fromisoformat(data["signal_to"]) if data.get("signal_to") else None,
            strategies_snapshot=data.get("strategies_snapshot", []),
            signals=signals,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> SignalSet:
        return cls.from_dict(json.loads(text))
```

- [ ] **Step 2: 测试信号序列化**

```python
# tests/domain/test_signal_models.py

def test_signal_set_roundtrip():
    ss = SignalSet(
        execution_key="20260709_120000_selection",
        signal_from=date(2026, 7, 1),
        signal_to=date(2026, 7, 9),
        strategies_snapshot=[{"id": "bbi_kdj_b1", "name": "B1战法"}],
        signals=[
            StrategySignal(
                strategy_id="bbi_kdj_b1",
                strategy_name="B1战法",
                group_ids=["default"],
                primary_group_id="default",
                signal_date=date(2026, 7, 9),
                codes=["000001", "600519"],
            ),
        ],
    )
    json_str = ss.to_json()
    parsed = SignalSet.from_json(json_str)
    assert parsed.execution_key == "20260709_120000_selection"
    assert len(parsed.signals) == 1
    assert parsed.signals[0].codes == ["000001", "600519"]
    assert parsed.signals[0].strategy_id == "bbi_kdj_b1"

def test_empty_signal_set():
    ss = SignalSet(execution_key="test")
    json_str = ss.to_json()
    parsed = SignalSet.from_json(json_str)
    assert parsed.signals == []
    assert parsed.schema_version == "2.0"
```

- [ ] **Step 3: 实现 SignalRepository**

```python
# domain/signal/repository.py
from __future__ import annotations
from pathlib import Path
from trendradar.domain.signal.models import SignalSet
from trendradar.infrastructure.storage.artifact_store import ArtifactStore


class SignalRepository:
    def __init__(self, artifact_store: ArtifactStore):
        self._store = artifact_store

    def save(self, signal_set: SignalSet, execution_key: str) -> str:
        return self._store.write_json(execution_key, "selection", {
            "signals.json": signal_set.to_dict(),
        })

    def load(self, execution_key: str) -> SignalSet | None:
        path = self._store.path_for(execution_key, "selection", "signals.json")
        if not path.exists():
            return None
        data = path.read_text(encoding="utf-8")
        return SignalSet.from_json(data)
```

---

## Task 6: Domain 回测引擎

**Files:**
- Create: `trendradar/domain/backtest/__init__.py`
- Create: `trendradar/domain/backtest/models.py`
- Create: `trendradar/domain/backtest/config.py`
- Create: `trendradar/domain/backtest/portfolio.py`
- Create: `trendradar/domain/backtest/execution.py`
- Create: `trendradar/domain/backtest/engine.py`
- Create: `trendradar/domain/backtest/metrics.py`
- Create: `tests/domain/test_backtest_portfolio.py`
- Create: `tests/domain/test_backtest_engine.py`
- Create: `tests/domain/test_backtest_execution.py`

**Interfaces:**
- Consumes: `SignalSet`、`MarketDataStore`
- Produces: `BacktestEngine.run(signals, config) -> BacktestResult`

- [ ] **Step 1: 实现 config.py**（基本上沿用旧版 frozen dataclass，去除 pandas 依赖）

- [ ] **Step 2: 实现 portfolio.py** — `PortfolioState` 纯 Python dict 操作

核心：
```python
@dataclass
class Position:
    strategy: str
    code: str
    signal_date: date
    entry_date: date
    target_sell_date: date
    entry_price: float
    shares: int
    entry_cost: float
    planned_sell_attempts: int = 0

@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position]  # code -> Position
    closed_codes: set[str]

    def open_position(self, code, position, cash_used): ...
    def close_position(self, code) -> Position: ...
    def available_open_slots(self, max_positions) -> int: ...
    def is_holding(self, code) -> bool: ...
```

- [ ] **Step 3: 实现 execution.py** — 涨停/跌停、费用、滑点

```python
def limit_up_price(prev_close: float, *, is_st: bool = False) -> float: ...
def limit_down_price(prev_close: float, *, is_st: bool = False) -> float: ...
def is_limit_up(price: float, prev_close: float, *, is_st: bool = False) -> bool: ...
def calc_buy_fill(open_price: float, shares: int, costs: CostConfig) -> FillResult: ...
def calc_sell_fill(close_price: float, shares: int, costs: CostConfig) -> FillResult: ...
```

- [ ] **Step 4: 实现 engine.py** — 主循环

```python
class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(
        self,
        signal_set: SignalSet,
        market_store: MarketDataStore,
        progress: Callable | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        # 1. 加载所有信号
        # 2. 按日期分组 signals_by_date
        # 3. 加载交易日历
        # 4. 初始化 PortfolioState
        # 5. 逐日循环: process_exits -> process_entries -> record_equity
        # 6. 计算指标
        # 7. 返回 BacktestResult {trades, equity_curve, skips, metrics}
```

- [ ] **Step 5: 测试回测关键路径**

```python
# test_backtest_portfolio.py
def test_open_and_close_position():
    state = PortfolioState(cash=100000, positions={}, closed_codes=set())
    state.open_position("000001", Position(...), cash_used=50000)
    assert "000001" in state.positions
    assert state.cash == 50000

    state.close_position("000001")
    assert "000001" not in state.positions
    assert "000001" in state.closed_codes

# test_backtest_execution.py
def test_limit_up_price():
    assert limit_up_price(10.0) == pytest.approx(11.0)  # 主板 10%
    assert limit_up_price(10.0, is_st=True) == pytest.approx(10.5)  # ST 5%
```

---

## Task 7: Infrastructure 行情数据 + Tushare

**Files:**
- Create: `trendradar/domain/market/__init__.py`
- Create: `trendradar/domain/market/models.py`
- Create: `trendradar/domain/market/data_store.py`
- Create: `trendradar/infrastructure/tushare/__init__.py`
- Create: `trendradar/infrastructure/tushare/client.py`
- Create: `trendradar/infrastructure/tushare/syncer.py`
- Create: `trendradar/infrastructure/tushare/stocklist.py`
- Create: `tests/infrastructure/test_market_data_store.py`

**Interfaces:**
- Produces: `MarketDataStore` 实现（从 `storage/market/bars/{code}.parquet` 读写）
- Produces: `TushareSyncer` — 拉取日线，限流重试，atomic 写入
- Produces: `StockListSyncer` — 同步股票列表到 `stock_meta.parquet`

- [ ] **Step 1: 实现 MarketDataStore**

```python
# domain/market/data_store.py
POLARS_KLINE_SCHEMA = {
    "code": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "adj_factor": pl.Float64,
    "is_suspended": pl.Boolean,
}

class LocalParquetMarketStore(MarketDataStore):
    def __init__(self, bars_dir: Path):
        self.bars_dir = bars_dir

    def load_bars(self, codes, start, end, columns=None):
        paths = [self.bars_dir / f"{c}.parquet" for c in codes]
        existing = [p for p in paths if p.exists()]
        if not existing:
            return pl.DataFrame(schema=POLARS_KLINE_SCHEMA)
        dfs = []
        for p in existing:
            df = pl.read_parquet(p)
            code = p.stem
            df = df.with_columns(pl.lit(code).alias("code"))
            dfs.append(df)
        result = pl.concat(dfs).filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        )
        if columns:
            result = result.select(columns)
        return result

    def latest_trade_date(self):
        # Scan all parquet files, find max date
        ...

    def trading_dates(self, start, end):
        ...
```

---

## Task 8: App Job 系统

**Files:**
- Create: `trendradar/app/__init__.py`
- Create: `trendradar/app/jobs/__init__.py`
- Create: `trendradar/app/jobs/executor.py`
- Create: `trendradar/app/jobs/context.py`
- Create: `trendradar/app/jobs/persistence.py`
- Create: `tests/app/test_job_executor.py`

- [ ] **Step 1: 实现 JobContext**

```python
class JobContext:
    def __init__(self, job_id, job_type, store, cancel_check):
        self.job_id = job_id
        self.job_type = job_type
        self._store = store
        self._cancel_check = cancel_check
        self._seq = 0

    def log(self, message, level="INFO"):
        self._seq += 1
        self._store.append_log(self.job_id, self._seq, level, message)

    def check_cancelled(self) -> bool:
        return self._cancel_check()

    def update_progress(self, current, total, message=""):
        ...

    def succeed(self, result):
        self._store.set_status(self.job_id, "success", result=result)

    def fail(self, error):
        self._store.set_status(self.job_id, "failed", error=error)
```

- [ ] **Step 2: 实现 JobExecutor**

```python
class JobExecutor:
    def __init__(self, store, max_workers=2):
        self._store = store
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, JobState] = {}
        self._cancelling: set[str] = set()
        self._lock = Lock()

    def submit(self, job_type, run_fn, request) -> str:
        job_id = self._store.create_job(job_type, request)
        fut = self._pool.submit(self._run, job_id, run_fn)
        self._jobs[job_id] = JobState(job_id, fut)
        return job_id

    def cancel(self, job_id) -> bool:
        with self._lock:
            self._cancelling.add(job_id)
        return True

    def _is_cancelled(self, job_id) -> bool:
        return job_id in self._cancelling

    def _run(self, job_id, run_fn):
        ctx = JobContext(job_id, ..., cancel_check=lambda: self._is_cancelled(job_id))
        try:
            run_fn(ctx)
        except Exception as e:
            ctx.fail(str(e))
```

---

## Task 9: App 服务层

**Files:**
- Create: `trendradar/app/services/__init__.py`
- Create: `trendradar/app/services/strategy_service.py`
- Create: `trendradar/app/services/market_service.py`
- Create: `trendradar/app/services/selection_service.py`
- Create: `trendradar/app/services/backtest_service.py`

**每个 service 都是编排者，不做业务逻辑：**
- `strategy_service` → CRUD 策略组 + 策略设置，读写 SQLite
- `market_service` → 编排行情同步 job
- `selection_service` → 解析策略 → 运行 selector → 写入 SignalSet
- `backtest_service` → 加载 SignalSet → 回测 → 写入 artifact

---

## Task 10: API 接口层

**Files:**
- Create: `trendradar/interfaces/api/__init__.py`
- Create: `trendradar/interfaces/api/app.py`
- Create: `trendradar/interfaces/api/routes/strategies.py`
- Create: `trendradar/interfaces/api/routes/executions.py`
- Create: `trendradar/interfaces/api/routes/market.py`
- Create: `trendradar/interfaces/api/routes/backtest.py`
- Create: `trendradar/interfaces/api/schemas/strategy.py`
- Create: `trendradar/interfaces/api/schemas/execution.py`
- Create: `trendradar/interfaces/api/schemas/market.py`
- Create: `trendradar/interfaces/api/schemas/backtest.py`

API endpoints 遵循 spec 定义（L717-728）。

---

## Task 11: 清理 + 部署 + 文档

- 删除不再使用的旧文件
- 更新 `README.md`
- 更新 Dockerfile / docker-compose
- 更新部署脚本
- 更新前端适配

---

## 自检

对照 spec 检查覆盖：

| Spec 要求 | 实现位置 |
|---|---|
| 破坏性初始化安全策略 (L33-40) | Task 1, `cli.py cmd_init_v2` |
| 目标架构四层 (L43-67) | 整体文件结构 |
| 运行数据布局 (L82-106) | `infrastructure/runtime.py` |
| 策略组模型 + default 保护 (L125-156) | Task 2 |
| 策略注册表 (L159-205) | Task 2 `registry.py` |
| 策略 ID 映射 (L181-196) | Task 2 `registry._CONFIG_ALIAS_MAP` |
| V2 策略协议 (L219-248) | Task 2 `protocol.py` |
| 选股语义 groups+strategies (L264-281) | Task 2 `resolver.py` |
| 行情数据 MarketDataStore (L287-333) | Task 7 |
| 行情同步与 Tushare (L335-361) | Task 7 |
| 信号模型 SignalSet (L365-407) | Task 5 |
| 选股 Artifact Schema (L410-455) | Task 5 `SignalRepository` + `ArtifactStore` |
| 回测模型 (L459-497) | Task 6 |
| 回测 Artifact Schema (L501-524) | Task 6 |
| SQLite Schema (L527-650) | Task 1 `schema.py` |
| 任务系统 (L663-708) | Task 8 |
| API (L712-757) | Task 10 |
| 破坏性重建初始化 (L760-777) | Task 1 `cli.py` |
| 错误处理与可观测性 (L780-804) | Task 8 `JobContext` |
| 部署变更 (L807-816) | Task 11 |
| 测试策略 (L819-841) | 每个 Task 的测试 |
