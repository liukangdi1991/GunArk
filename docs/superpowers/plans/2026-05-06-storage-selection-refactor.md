# Storage Selection Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended, only after explicit user approval) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split storage and selection internals into focused modules without changing Web API behavior or existing import paths.

**Architecture:** Keep `AppStorage`, `selection.strategies`, and `selection.runners` as compatibility facades. Move implementation into smaller modules behind those facades. Extract repeated Polars expressions for trend lines, daily filters, and common rolling metrics so runner logic is easier to verify.

**Tech Stack:** Python 3.13, SQLite, Polars, FastAPI service layer.

---

## Scope

In scope:

- Split `core/storage.py` internals into repository/helper modules.
- Split `selection/strategies.py` into `selection/selectors/`.
- Split `selection/runners.py` into `selection/strategy_runners/`.
- Extract common prefilter expressions used by runners.

Out of scope:

- Docker runtime verification.
- Data cleanup from git.
- Strategy behavior changes.
- Web route changes.
- Database schema changes.

## File Structure

- Modify: `core/storage.py`
- Create: `core/storage_connection.py`
- Create: `core/storage_artifacts.py`
- Create: `core/storage_records.py`
- Modify: `selection/strategies.py`
- Modify: `selection/runners.py`
- Create: `selection/selectors/__init__.py`
- Create: `selection/selectors/*.py`
- Create: `selection/strategy_runners/__init__.py`
- Create: `selection/strategy_runners/*.py`
- Create: `selection/strategy_runners/expressions.py`
- Create: `tests/test_storage_facade.py`
- Create: `tests/test_selection_compat.py`

---

### Task 1: Add Minimal Regression Tests

- [ ] Create `tests/test_storage_facade.py` to verify `AppStorage.ensure_ready()` initializes SQLite and objects root.
- [ ] Create `tests/test_selection_compat.py` to verify existing imports from `selection.strategies` and `selection.runners` remain valid.
- [ ] Run `.venv/bin/python -m pytest tests/test_storage_facade.py tests/test_selection_compat.py -q`.

### Task 2: Split Storage Helpers

- [ ] Create `core/storage_connection.py` for SQLite connection and artifact path/key helpers.
- [ ] Create `core/storage_records.py` for row-to-dict conversion helpers.
- [ ] Create `core/storage_artifacts.py` for artifact registration/listing/path deletion helpers.
- [ ] Keep `AppStorage` public methods and method signatures unchanged.
- [ ] Run `.venv/bin/python -m py_compile core/*.py` and the storage test.

### Task 3: Split Selection Strategies

- [ ] Create `selection/selectors/` modules and move selector classes one by one.
- [ ] Turn `selection/strategies.py` into a compatibility shim exporting the same class names.
- [ ] Run selection compatibility test and `py_compile`.

### Task 4: Split Selection Runners

- [ ] Create `selection/strategy_runners/` modules for base/default/specialized runners.
- [ ] Turn `selection/runners.py` into a compatibility shim exporting `StrategySelectionRunner`, `DefaultSelectionRunner`, and `build_strategy_runner`.
- [ ] Run selection compatibility test and `py_compile`.

### Task 5: Extract Shared Prefilter Expressions

- [ ] Create `selection/strategy_runners/expressions.py`.
- [ ] Move repeated Polars expressions for short-term trend line, long-term bull-bear line, MA60, previous close, and daily range filters.
- [ ] Replace repeated runner expressions with helpers without changing numeric formulas.
- [ ] Run import tests, `py_compile`, and a strategy listing smoke test.

## Verification

Run:

```bash
.venv/bin/python -m pytest tests/test_storage_facade.py tests/test_selection_compat.py -q
.venv/bin/python -m py_compile core/*.py selection/*.py selection/selectors/*.py selection/strategy_runners/*.py
.venv/bin/python -c "from web.app import app; print(app.title)"
.venv/bin/python -c "from web.services.strategy_service import list_strategies; print(len(list_strategies()['strategies']))"
```

Expected:

```text
tests pass
py_compile exits 0
趋势雷达 TrendRadar API
integer greater than 0
```

## Self-Review

- Existing public imports stay compatible.
- No schema changes are introduced.
- No selection formulas are intentionally changed.
- Docker/runtime is not included in this focused plan.
