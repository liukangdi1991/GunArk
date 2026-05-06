# Web Service Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended, only after explicit user approval) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the Web service runtime boundary, split the oversized storage facade internally, and split selection strategies/runners without changing user-facing Web behavior.

**Architecture:** Keep FastAPI/React APIs stable while moving mutable runtime data under a single runtime root. Keep `AppStorage` as the public facade, but move internals into focused repositories. Keep existing `selection.strategies` and `selection.runners` import paths as compatibility shims while moving real implementations into smaller modules.

**Tech Stack:** FastAPI, React + TypeScript, Polars, SQLite, Docker Compose, PyInstaller launcher.

---

## Scope

In scope:

- Docker/runtime directory boundary.
- `core/storage.py` internal split while preserving external method names.
- `selection/strategies.py` and `selection/runners.py` split while preserving strategy results.

Out of scope:

- Removing tracked `db/`, `storage/`, or log files from git.
- Changing Web API routes.
- Changing selection strategy behavior.
- Changing backtest calculation logic.

## File Structure

Docker/runtime files:

- Modify: `docker-compose.yml`
- Modify: `Dockerfile`
- Modify: `scripts/_compose.sh`
- Modify: `scripts/install.sh`
- Modify: `scripts/start.sh`
- Modify: `scripts/restart.sh`
- Modify: `scripts/update.sh`
- Modify: `README.md`

Storage files:

- Modify: `core/storage.py`
- Create: `core/storage_connection.py`
- Create: `core/storage_artifacts.py`
- Create: `core/storage_executions.py`
- Create: `core/storage_selection.py`
- Create: `core/storage_backtest.py`
- Create: `core/storage_execution_logs.py`

Selection files:

- Modify: `selection/strategies.py`
- Modify: `selection/runners.py`
- Create: `selection/selectors/__init__.py`
- Create: `selection/selectors/bbi_kdj.py`
- Create: `selection/selectors/super_b1.py`
- Create: `selection/selectors/peak_kdj.py`
- Create: `selection/selectors/bbi_short_long.py`
- Create: `selection/selectors/ma60_volume_wave.py`
- Create: `selection/selectors/balance.py`
- Create: `selection/selectors/perfect_b1.py`
- Create: `selection/selectors/big_bullish_volume.py`
- Create: `selection/strategy_runners/__init__.py`
- Create: `selection/strategy_runners/base.py`
- Create: `selection/strategy_runners/default.py`
- Create: `selection/strategy_runners/bbi_kdj.py`
- Create: `selection/strategy_runners/prefilters.py`
- Create: `selection/strategy_runners/factory.py`

Validation files:

- Create: `requirements-dev.txt`
- Create: `tests/test_runtime_root.py`
- Create: `tests/test_storage_facade.py`
- Create: `tests/test_selection_imports.py`

---

### Task 1: Add Dev Test Harness

**Files:**

- Create: `requirements-dev.txt`
- Create: `tests/test_runtime_root.py`
- Create: `tests/test_storage_facade.py`
- Create: `tests/test_selection_imports.py`

- [ ] **Step 1: Create dev requirements**

Create `requirements-dev.txt`:

```text
pytest>=8.3.0
```

- [ ] **Step 2: Create runtime-root smoke test**

Create `tests/test_runtime_root.py`:

```python
from __future__ import annotations

import importlib


def test_runtime_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    import core.runtime as runtime

    importlib.reload(runtime)

    assert runtime.runtime_root() == tmp_path.resolve()
```

- [ ] **Step 3: Create storage facade smoke test**

Create `tests/test_storage_facade.py`:

```python
from __future__ import annotations

from core.storage import AppStorage


def test_storage_facade_initializes_schema(tmp_path):
    storage = AppStorage(tmp_path / "storage")
    storage.ensure_ready()

    assert storage.db_path.exists()
    assert storage.objects_root.exists()
```

- [ ] **Step 4: Create selection import compatibility test**

Create `tests/test_selection_imports.py`:

```python
from __future__ import annotations


def test_strategy_imports_stay_compatible():
    from selection.strategies import BBIKDJSelector, PerfectB1Selector, ZXDKXBalanceSelector

    assert BBIKDJSelector is not None
    assert PerfectB1Selector is not None
    assert ZXDKXBalanceSelector is not None


def test_runner_factory_import_stays_compatible():
    from selection.runners import build_strategy_runner
    from selection.strategies import PerfectB1Selector

    runner = build_strategy_runner(PerfectB1Selector())

    assert runner is not None
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_runtime_root.py tests/test_storage_facade.py tests/test_selection_imports.py -q
```

Expected:

```text
4 passed
```

---

### Task 2: Move Docker Runtime To `/data`

**Files:**

- Modify: `docker-compose.yml`
- Modify: `Dockerfile`
- Modify: `scripts/_compose.sh`
- Modify: `README.md`

- [ ] **Step 1: Update Docker Compose volume boundary**

Replace single-file mounts in `docker-compose.yml`:

```yaml
services:
  trend-radar:
    build:
      context: .
      dockerfile: Dockerfile
    image: trend-radar:latest
    container_name: trend-radar
    env_file:
      - ./deploy/.env
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      TREND_RADAR_RUNTIME_ROOT: /data
    ports:
      - "${APP_PORT:-8818}:8000"
    volumes:
      - ./deploy/data:/data
    restart: unless-stopped
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/market-data/status', timeout=3)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

- [ ] **Step 2: Start Docker through launcher**

Replace the `CMD` in `Dockerfile`:

```dockerfile
CMD ["python", "trend_radar_launcher.py", "--runtime-dir", "/data", "--host", "0.0.0.0", "--port", "8000"]
```

Expected effect:

- Runtime files are read/written under `/data`.
- App resources remain under `/app`.
- Docker no longer bind-mounts `/app/stocklist.csv`.

- [ ] **Step 3: Migrate deploy layout helper**

Update `scripts/_compose.sh` so `ensure_deploy_layout` creates:

```text
deploy/data/db/
deploy/data/storage/cache/
deploy/data/storage/objects/
deploy/data/configs.json
deploy/data/stocklist.csv
```

Use this logic:

```bash
if [ ! -f "${PROJECT_ROOT}/deploy/data/configs.json" ]; then
  if [ -f "${PROJECT_ROOT}/deploy/configs.json" ]; then
    cp "${PROJECT_ROOT}/deploy/configs.json" "${PROJECT_ROOT}/deploy/data/configs.json"
  else
    cp "${PROJECT_ROOT}/configs.json" "${PROJECT_ROOT}/deploy/data/configs.json"
  fi
fi

if [ ! -f "${PROJECT_ROOT}/deploy/data/stocklist.csv" ]; then
  if [ -f "${PROJECT_ROOT}/deploy/stocklist.csv" ]; then
    cp "${PROJECT_ROOT}/deploy/stocklist.csv" "${PROJECT_ROOT}/deploy/data/stocklist.csv"
  else
    cp "${PROJECT_ROOT}/stocklist.csv" "${PROJECT_ROOT}/deploy/data/stocklist.csv"
  fi
fi
```

- [ ] **Step 4: Keep env file at `deploy/.env`**

Do not move:

```text
deploy/.env
deploy/.env.example
```

Reason: Compose can still load env from `deploy/.env`, while app runtime files live under `deploy/data`.

- [ ] **Step 5: Validate scripts**

Run:

```bash
bash -n scripts/_compose.sh scripts/install.sh scripts/start.sh scripts/restart.sh scripts/update.sh
```

Expected:

```text
no output
```

- [ ] **Step 6: Validate runtime init without Docker**

Run:

```bash
tmpdir="$(mktemp -d)"
TREND_RADAR_RUNTIME_ROOT="$tmpdir" .venv/bin/python trend_radar_launcher.py --init-only
test -f "$tmpdir/storage/app.db"
test -f "$tmpdir/configs.json"
test -f "$tmpdir/stocklist.csv"
```

Expected:

```text
趋势雷达 TrendRadar 初始化完成: <tmpdir>
```

---

### Task 3: Split `core/storage.py` Behind The Existing Facade

**Files:**

- Modify: `core/storage.py`
- Create: `core/storage_connection.py`
- Create: `core/storage_artifacts.py`
- Create: `core/storage_executions.py`
- Create: `core/storage_selection.py`
- Create: `core/storage_backtest.py`
- Create: `core/storage_execution_logs.py`
- Test: `tests/test_storage_facade.py`

- [ ] **Step 1: Extract connection and path helpers**

Create `core/storage_connection.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


class StorageConnection:
    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root)
        self.db_path = self.storage_root / "app.db"
        self.objects_root = self.storage_root / "objects"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        return conn

    def storage_key(self, path: Path) -> str:
        path = path.resolve()
        try:
            return str(path.relative_to(self.objects_root.resolve()))
        except ValueError:
            return str(path)

    def artifact_path(self, storage_key: str) -> Path:
        path = Path(storage_key)
        return path if path.is_absolute() else self.objects_root / path
```

- [ ] **Step 2: Update `AppStorage` to use `StorageConnection`**

Keep public attributes unchanged:

```python
self.storage_root = Path(storage_root)
self.db_path = self.storage_root / "app.db"
self.objects_root = self.storage_root / "objects"
```

Add:

```python
self._connection = StorageConnection(self.storage_root)
```

Change `_connect()` to:

```python
def _connect(self) -> sqlite3.Connection:
    return self._connection.connect()
```

Change `artifact_path()` and `storage_key()` to delegate:

```python
def artifact_path(self, storage_key: str) -> Path:
    return self._connection.artifact_path(storage_key)

def storage_key(self, path: Path) -> str:
    return self._connection.storage_key(path)
```

- [ ] **Step 3: Extract artifact repository**

Create `core/storage_artifacts.py` with methods moved from `AppStorage`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from core.storage_utils import execution_type_from_legacy, sha256


class ArtifactRepository:
    def __init__(self, owner) -> None:
        self.owner = owner

    def register_artifact(self, *, execution_key: str, artifact_type: str, path: Path, mime_type: str, run_type: str = "backtest") -> None:
        self.owner.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        stat = path.stat()
        with self.owner._connect() as conn:
            execution_id = self.owner._get_execution_id(conn, execution_key)
            if execution_id is None:
                execution_id = self.owner._upsert_execution(
                    conn,
                    execution_key=execution_key,
                    execution_type=execution_type_from_legacy(run_type),
                    status="success",
                    created_at=now,
                    finished_at=now,
                    object_dir_key=self.owner._execution_object_key(path.parent),
                )
            conn.execute(
                """
                insert into artifacts (
                    execution_id, artifact_scope, artifact_type, storage_key,
                    mime_type, size_bytes, checksum, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(execution_id, artifact_scope, artifact_type) do update set
                    storage_key = excluded.storage_key,
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    checksum = excluded.checksum,
                    created_at = excluded.created_at
                """,
                (
                    execution_id,
                    str(run_type),
                    artifact_type,
                    self.owner.storage_key(path),
                    mime_type,
                    stat.st_size,
                    sha256(path),
                    now,
                ),
            )
```

After moving, `AppStorage.register_artifact()` becomes:

```python
def register_artifact(self, **kwargs) -> None:
    return self._artifacts.register_artifact(**kwargs)
```

- [ ] **Step 4: Extract selection/backtest/log repositories gradually**

Move one public method at a time, preserving signatures:

```text
record_selection_result -> SelectionRepository
list_selection_results -> SelectionRepository
get_selection_result -> SelectionRepository
delete_selection_results -> SelectionRepository
record_backtest_result -> BacktestRepository
list_backtest_results -> BacktestRepository
get_backtest_result -> BacktestRepository
delete_backtest_results -> BacktestRepository
record_execution_log_link -> ExecutionLogRepository
```

For each moved method:

```python
def list_selection_results(self, limit: int = 50) -> list[dict[str, object]]:
    return self._selection.list_selection_results(limit=limit)
```

- [ ] **Step 5: Run storage regression checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_storage_facade.py -q
.venv/bin/python -m py_compile core/storage.py core/storage_connection.py core/storage_artifacts.py core/storage_executions.py core/storage_selection.py core/storage_backtest.py core/storage_execution_logs.py
```

Expected:

```text
1 passed
```

---

### Task 4: Split Selection Strategies Without Breaking Imports

**Files:**

- Modify: `selection/strategies.py`
- Create: `selection/selectors/__init__.py`
- Create: `selection/selectors/*.py`
- Test: `tests/test_selection_imports.py`

- [ ] **Step 1: Create selectors package**

Create `selection/selectors/__init__.py`:

```python
from __future__ import annotations

from selection.selectors.bbi_kdj import BBIKDJSelector
from selection.selectors.super_b1 import SuperB1Selector
from selection.selectors.peak_kdj import PeakKDJSelector
from selection.selectors.bbi_short_long import BBIShortLongSelector
from selection.selectors.ma60_volume_wave import MA60CrossVolumeWaveSelector
from selection.selectors.balance import LongTermBullBearLineBalanceSelector, ZXDKXBalanceSelector
from selection.selectors.perfect_b1 import PerfectB1Selector
from selection.selectors.big_bullish_volume import BigBullishVolumeSelector

__all__ = [
    "BBIKDJSelector",
    "SuperB1Selector",
    "PeakKDJSelector",
    "BBIShortLongSelector",
    "MA60CrossVolumeWaveSelector",
    "LongTermBullBearLineBalanceSelector",
    "ZXDKXBalanceSelector",
    "PerfectB1Selector",
    "BigBullishVolumeSelector",
]
```

- [ ] **Step 2: Move classes one by one**

Move each class from `selection/strategies.py` into its target file. Do not change class bodies in this task.

Mapping:

```text
BBIKDJSelector -> selection/selectors/bbi_kdj.py
SuperB1Selector -> selection/selectors/super_b1.py
PeakKDJSelector -> selection/selectors/peak_kdj.py
BBIShortLongSelector -> selection/selectors/bbi_short_long.py
MA60CrossVolumeWaveSelector -> selection/selectors/ma60_volume_wave.py
LongTermBullBearLineBalanceSelector -> selection/selectors/balance.py
PerfectB1Selector -> selection/selectors/perfect_b1.py
BigBullishVolumeSelector -> selection/selectors/big_bullish_volume.py
```

- [ ] **Step 3: Turn `selection/strategies.py` into compatibility shim**

Replace `selection/strategies.py` with:

```python
from __future__ import annotations

from selection.selectors import (
    BBIKDJSelector,
    BBIShortLongSelector,
    BigBullishVolumeSelector,
    LongTermBullBearLineBalanceSelector,
    MA60CrossVolumeWaveSelector,
    PeakKDJSelector,
    PerfectB1Selector,
    SuperB1Selector,
    ZXDKXBalanceSelector,
)

__all__ = [
    "BBIKDJSelector",
    "SuperB1Selector",
    "PeakKDJSelector",
    "BBIShortLongSelector",
    "MA60CrossVolumeWaveSelector",
    "LongTermBullBearLineBalanceSelector",
    "ZXDKXBalanceSelector",
    "PerfectB1Selector",
    "BigBullishVolumeSelector",
]
```

- [ ] **Step 4: Run import compatibility test**

Run:

```bash
.venv/bin/python -m pytest tests/test_selection_imports.py -q
.venv/bin/python -m py_compile selection/strategies.py selection/selectors/*.py
```

Expected:

```text
2 passed
```

---

### Task 5: Split Selection Runners Without Breaking Factory

**Files:**

- Modify: `selection/runners.py`
- Create: `selection/strategy_runners/__init__.py`
- Create: `selection/strategy_runners/base.py`
- Create: `selection/strategy_runners/default.py`
- Create: `selection/strategy_runners/bbi_kdj.py`
- Create: `selection/strategy_runners/prefilters.py`
- Create: `selection/strategy_runners/factory.py`
- Test: `tests/test_selection_imports.py`

- [ ] **Step 1: Create runner package exports**

Create `selection/strategy_runners/__init__.py`:

```python
from __future__ import annotations

from selection.strategy_runners.base import StrategySelectionRunner
from selection.strategy_runners.default import DefaultSelectionRunner
from selection.strategy_runners.factory import build_strategy_runner

__all__ = [
    "StrategySelectionRunner",
    "DefaultSelectionRunner",
    "build_strategy_runner",
]
```

- [ ] **Step 2: Move base classes**

Move `StrategySelectionRunner` to `selection/strategy_runners/base.py`.

Move `DefaultSelectionRunner` to `selection/strategy_runners/default.py`.

Do not change behavior in this task.

- [ ] **Step 3: Move concrete runner classes**

Move concrete classes into focused files:

```text
BBIKDJSelectionRunner -> selection/strategy_runners/bbi_kdj.py
SuperB1SelectionRunner -> selection/strategy_runners/prefilters.py
PeakKDJSelectionRunner -> selection/strategy_runners/prefilters.py
BBIShortLongSelectionRunner -> selection/strategy_runners/prefilters.py
MA60CrossVolumeWaveSelectionRunner -> selection/strategy_runners/prefilters.py
BigBullishVolumeSelectionRunner -> selection/strategy_runners/prefilters.py
ZXDKXBalanceSelectionRunner -> selection/strategy_runners/prefilters.py
PerfectB1SelectionRunner -> selection/strategy_runners/prefilters.py
```

- [ ] **Step 4: Move factory**

Create `selection/strategy_runners/factory.py` with the existing `build_strategy_runner()` logic.

- [ ] **Step 5: Turn `selection/runners.py` into compatibility shim**

Replace `selection/runners.py` with:

```python
from __future__ import annotations

from selection.strategy_runners import (
    DefaultSelectionRunner,
    StrategySelectionRunner,
    build_strategy_runner,
)

__all__ = [
    "StrategySelectionRunner",
    "DefaultSelectionRunner",
    "build_strategy_runner",
]
```

- [ ] **Step 6: Run runner compatibility test**

Run:

```bash
.venv/bin/python -m pytest tests/test_selection_imports.py -q
.venv/bin/python -m py_compile selection/runners.py selection/strategy_runners/*.py
```

Expected:

```text
2 passed
```

---

### Task 6: Behavior Regression Checks

**Files:**

- No new files.

- [ ] **Step 1: Build frontend**

Run:

```bash
npm run build
```

Expected:

```text
✓ built
```

- [ ] **Step 2: Compile backend**

Run:

```bash
.venv/bin/python -m py_compile \
  core/*.py \
  web/app.py web/routes/*.py web/controllers/*.py web/services/*.py web/schemas/*.py \
  selection/*.py selection/selectors/*.py selection/strategy_runners/*.py \
  backtest/*.py backtest/data/*.py backtest/reports/*.py backtest/analytics/*.py
```

Expected:

```text
no output
```

- [ ] **Step 3: Smoke test API import**

Run:

```bash
.venv/bin/python -c "from web.app import app; print(app.title)"
```

Expected:

```text
趋势雷达 TrendRadar API
```

- [ ] **Step 4: Smoke test selection latest-date path**

Run:

```bash
.venv/bin/python -c "from web.services.strategy_service import list_strategies; print(len(list_strategies()['strategies']))"
```

Expected:

```text
an integer greater than 0
```

- [ ] **Step 5: Smoke test Docker scripts**

Run:

```bash
bash -n scripts/_compose.sh scripts/install.sh scripts/start.sh scripts/restart.sh scripts/update.sh scripts/stop.sh scripts/logs.sh scripts/backup.sh
```

Expected:

```text
no output
```

---

## Self-Review

Spec coverage:

- Docker/runtime boundary is covered by Task 2.
- Storage split is covered by Task 3 while preserving `AppStorage`.
- Selection split is covered by Tasks 4 and 5 while preserving imports.
- Data cleanup is explicitly out of scope.

Placeholder scan:

- No task uses TBD/TODO/fill-in placeholders.
- Each task has concrete file paths and verification commands.

Type consistency:

- Public method names stay on `AppStorage`.
- Existing import paths `selection.strategies` and `selection.runners` remain valid.
- Runtime root remains `TREND_RADAR_RUNTIME_ROOT`, already used by `core/runtime.py`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-web-service-refactor.md`.

Execution options:

1. **Subagent-Driven** - Recommended by Superpowers, but requires explicit user approval to use subagents.
2. **Inline Execution** - Execute tasks in this session using `Superpowers:executing-plans`, with checkpoints after each task.
