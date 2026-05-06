# Runner Polars Formula Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move runner-level Polars calculation formulas into shared expression helpers while keeping strategy composition inside each runner.

**Architecture:** `selection/strategy_runners/expressions.py` owns reusable `pl.Expr` formulas. Runner modules import helpers and only decide which formulas/conditions to combine for each strategy.

**Tech Stack:** Python, Polars, pytest.

---

### Task 1: Expand Polars Formula Helpers

**Files:**
- Modify: `selection/strategy_runners/expressions.py`

- [x] Add helpers for rolling max/min/sum, rolling flag existence, previous rolling mean, volume spike flags, volume step-down count, recent volume low, and KDJ RSV bounds.
- [x] Keep all helpers pure `pl.Expr` builders with no strategy-specific side effects.

### Task 2: Replace Raw Formula Logic In Runners

**Files:**
- Modify: `selection/strategy_runners/default.py`
- Modify: `selection/strategy_runners/bbi_kdj.py`
- Modify: `selection/strategy_runners/prefilters.py`

- [x] Replace direct `.rolling_*`, `.shift(1)`, and volume flag formulas with helpers.
- [x] Keep runner-level composition and strategy thresholds in the runner.
- [x] Do not change selector behavior or formulas in `selection/selectors/`.

### Task 3: Verify Behavior

**Files:**
- Test: `tests/test_selection_compat.py`
- Test: `tests/test_storage_facade.py`

- [x] Run compatibility tests.
- [x] Run backend compile checks.
- [x] Run real-data smoke checks for representative runners.
