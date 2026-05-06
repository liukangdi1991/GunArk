# Selection Package Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `selection/` so formulas, selectors, and execution runners have separate, clear directories without changing Web behavior.

**Architecture:** Move reusable formulas into `selection/formulas/`, move runner execution code into `selection/execution/`, and split the large prefilter runner module into one file per runner. Keep old public import paths (`selection.runners`, `selection.strategies`, `selection.indicators`) as compatibility shims.

**Tech Stack:** Python, Polars, pytest.

---

### Task 1: Move Formula Modules

**Files:**
- Create: `selection/formulas/__init__.py`
- Move: `selection/strategy_runners/expressions.py` -> `selection/formulas/expressions.py`
- Move: `selection/indicators.py` -> `selection/formulas/indicators.py`
- Modify: `selection/indicators.py`

- [x] Move Polars expression helpers out of runner package.
- [x] Move selector indicator calculations into formulas package.
- [x] Keep `selection.indicators` as a compatibility shim.

### Task 2: Move Runner Execution Package

**Files:**
- Create: `selection/execution/__init__.py`
- Move: `selection/strategy_runners/base.py` -> `selection/execution/base.py`
- Move: `selection/strategy_runners/default.py` -> `selection/execution/default.py`
- Move: `selection/strategy_runners/bbi_kdj.py` -> `selection/execution/runners/bbi_kdj.py`
- Move: `selection/strategy_runners/factory.py` -> `selection/execution/factory.py`
- Modify: `selection/runners.py`

- [x] Move execution framework out of `strategy_runners`.
- [x] Update imports to `selection.execution`.
- [x] Keep `selection.runners` as a compatibility shim.

### Task 3: Split Prefilter Runners

**Files:**
- Create: `selection/execution/runners/super_b1.py`
- Create: `selection/execution/runners/peak_kdj.py`
- Create: `selection/execution/runners/bbi_short_long.py`
- Create: `selection/execution/runners/ma60_volume_wave.py`
- Create: `selection/execution/runners/balance.py`
- Create: `selection/execution/runners/perfect_b1.py`
- Create: `selection/execution/runners/big_bullish_volume.py`
- Create: `selection/execution/runners/__init__.py`
- Delete: `selection/strategy_runners/prefilters.py`
- Delete: `selection/strategy_runners/__init__.py`

- [x] Move each runner class into a focused file.
- [x] Update factory imports to the split runner files.
- [x] Remove the old `strategy_runners` package.

### Task 4: Verify Compatibility

**Files:**
- Test: `tests/test_selection_compat.py`

- [x] Run selection compatibility tests.
- [x] Run backend compile checks.
- [x] Run Web app import smoke test.
- [x] Run real-data selection smoke test for all configured strategies.
