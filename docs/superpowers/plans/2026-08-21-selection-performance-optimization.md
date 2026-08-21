# Selection Performance Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut full-market selection from 45 min/day to <30 s (58-day range: hours → ~2 min) with behavior-equivalent results.

**Architecture:** Two-phase selector interface (`warmup(market_data) -> WarmupResult` once, `select_day(context, warmup)` per trading day). Indicators computed once; per-candidate history access via `partition_by("code")` dict instead of full-table `filter(code==x)` (removes the 96% bottleneck). Runner orchestrates warmup once then daily loop.

**Tech Stack:** Python 3.11+, polars, ThreadPoolExecutor (unused by default).

**Spec:** `docs/superpowers/specs/2026-08-21-selection-performance-optimization.md` (v9)

**Critical equivalence constraint:** Selectors judge the **last row of each code's full history** (`hist.row(-1)`), NOT the `trade_date` row — `trade_date` only labels the result. This is existing behavior (58-day runs repeat the same end-day judgment each day). `select_day` must keep it: judge `row(-1)` of the grouped history, label with `context.trade_date`.

---

## Task 1: Deterministic assertion baseline (behavior-equivalence benchmark)

**Files:**
- Create: `tests/domain/test_selectors/test_deterministic_baseline.py`
- Test: the new file

- [ ] **Step 1: Write the failing test (baseline capture)**

```python
"""Deterministic baseline: fixed input -> exact selected_codes, captured BEFORE
the selector refactor so the optimization can prove behavior equivalence.

make_ohlcv_df uses np.random.seed(42), so the data is deterministic.
"""

import pytest
import polars as pl
from datetime import date

from .helpers import (
    make_bbi_kdj_b1_defn, make_super_b1_defn, make_bbi_short_long_defn,
    make_peak_kdj_defn, make_ma60_volume_wave_defn, make_zxdkx_balance_defn,
    make_perfect_b1_defn, make_big_bullish_volume_defn,
    make_volume_spike_balance_defn, make_empty_df, make_ohlcv_df,
)
from trendradar.domain.strategy.protocol import SelectionContext


def _ctx(df):
    return SelectionContext(
        trade_date=date(2026, 7, 9),
        market_data=df,
        candidate_codes=df["code"].unique().to_list(),
    )


def _select_codes(defn, df):
    return defn.selector_class(defn).select(_ctx(df)).selected_codes


# Two deterministic fixtures: one stock (000001) with 150 days uptrend,
# two stocks (000001, 600519) with different trends.
def _df_single():
    return make_ohlcv_df("000001", 150, trend=0.002)


def _df_double():
    a = make_ohlcv_df("000001", 150, trend=0.002)
    b = make_ohlcv_df("600519", 150, trend=0.001, start_price=100.0)
    return pl.concat([a, b])


@pytest.mark.parametrize(
    "defn_maker, df_builder",
    [
        (make_bbi_kdj_b1_defn, _df_single),
        (make_super_b1_defn, _df_single),
        (make_bbi_short_long_defn, _df_single),
        (make_peak_kdj_defn, _df_single),
        (make_ma60_volume_wave_defn, _df_single),
        (make_zxdkx_balance_defn, _df_single),
        (make_perfect_b1_defn, _df_single),
        (make_big_bullish_volume_defn, _df_single),
        (make_volume_spike_balance_defn, _df_single),
        (make_bbi_kdj_b1_defn, _df_double),
        (make_super_b1_defn, _df_double),
        (make_bbi_short_long_defn, _df_double),
        (make_peak_kdj_defn, _df_double),
        (make_ma60_volume_wave_defn, _df_double),
        (make_zxdkx_balance_defn, _df_double),
        (make_perfect_b1_defn, _df_double),
        (make_big_bullish_volume_defn, _df_double),
        (make_volume_spike_balance_defn, _df_double),
    ],
)
def test_deterministic_baseline(defn_maker, df_builder):
    defn = defn_maker()
    codes = _select_codes(defn, df_builder())
    # Baseline captured pre-refactor; assert exact list so the refactor must
    # reproduce it bit-for-bit.
    assert isinstance(codes, list)
```

- [ ] **Step 2: Run baseline and capture exact values**

Run: `.venv/bin/python -m pytest tests/domain/test_selectors/test_deterministic_baseline.py -q`
Expected: PASS (18 tests). Now **replace the loose assertion** with the exact captured lists — run with `-s`/print and hard-code each `codes` value. This is the behavior-equivalence benchmark.

- [ ] **Step 3: Commit**

```bash
git add tests/domain/test_selectors/test_deterministic_baseline.py
git commit -m "test: deterministic selection baseline (behavior-equivalence benchmark)"
```

---

## Task 2: Protocol migration

**Files:**
- Modify: `trendradar/domain/strategy/protocol.py`
- Test: `tests/domain/test_strategy_resolver.py` (imports only), existing selector tests still pass (selectors still implement `select`; the Protocol is structural)

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_protocol_shape.py
from dataclasses import FrozenInstanceError
import pytest
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionContext, SelectionResult, WarmupResult, SelectionStrategy,
)


def test_warmup_result_is_frozen_dataclass():
    w = WarmupResult(grouped={"000001": pl.DataFrame()})
    with pytest.raises(FrozenInstanceError):
        w.grouped = {}
    assert set(WarmupResult.__dataclass_fields__) == {"grouped"}


def test_selection_context_has_no_get_data_dict():
    import dataclasses
    assert "get_data_dict" not in {f.name for f in dataclasses.fields(SelectionContext)}


def test_selection_result_fields_unchanged():
    import dataclasses
    assert {f.name for f in dataclasses.fields(SelectionResult)} == {
        "strategy_id", "strategy_name", "trade_date", "selected_codes", "elapsed_seconds",
    }


def test_protocol_has_warmup_and_select_day():
    assert "warmup" in getattr(SelectionStrategy, "__annotations__", {})
    assert "select_day" in getattr(SelectionStrategy, "__annotations__", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/domain/test_protocol_shape.py -q`
Expected: FAIL — `WarmupResult` undefined, `get_data_dict` still on `SelectionContext`.

- [ ] **Step 3: Write minimal implementation**

`trendradar/domain/strategy/protocol.py`:

```python
"""Selection strategy protocol and context types.

V2 performance model: warmup once per selection, select_day per trading day.
Indicators are computed once in warmup; per-candidate history is accessed
through the grouped WarmupResult (partition_by code) instead of full-table
filters. Selectors are stateless beyond self.definition; warmup results are
passed explicitly, never stored on the selector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

import polars as pl


@dataclass(frozen=True)
class WarmupResult:
    """Precomputed per-code data (indicator columns included), grouped by code.

    group[code] is that stock's full history, ordered by [code, date], with
    indicator columns attached. Must be read-only during select_day.
    """
    grouped: dict[str, pl.DataFrame]


@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    candidate_codes: list[str] | None = None   # None = full market (runner always passes a list today)
    market_data: pl.DataFrame = field(default_factory=pl.DataFrame)


@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float


class SelectionStrategy(Protocol):
    definition: "StrategyDefinition"

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        """Precompute indicator columns and group by code (called once)."""
        ...

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        """Judge one trading day from precomputed data (called per day)."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/domain/test_protocol_shape.py -q`
Expected: PASS. Also run `tests/domain -q` — existing selector tests still pass (structural Protocol; selectors unchanged).

- [ ] **Step 5: Commit**

```bash
git add trendradar/domain/strategy/protocol.py tests/domain/test_protocol_shape.py
git commit -m "feat: two-phase selection protocol (warmup/select_day, WarmupResult)"
```

---

## Task 3: Refactor bbi_kdj_b1 selector

**Files:**
- Modify: `trendradar/domain/strategy/selectors/bbi_kdj_b1.py`
- Test: `tests/domain/test_selectors/test_bbi_kdj_b1.py`, `test_deterministic_baseline.py`

- [ ] **Step 1: Adapt the tests (RED)**

`test_bbi_kdj_b1.py`: `_make_context` drops `get_data_dict`; each `select(ctx)` call becomes `sel.warmup(df)` then `sel.select_day(ctx, warmup)`:

```python
def _run(defn, df):
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    return sel.select_day(_make_context(df), warmup)
```

Replace `defn.selector_class(defn).select(ctx)` with `_run(defn, df)` in both tests. Baseline test still uses `.select()` — it must be migrated to `warmup`+`select_day` in this task too (keep the exact captured lists).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/domain/test_selectors/test_bbi_kdj_b1.py tests/domain/test_selectors/test_deterministic_baseline.py -q`
Expected: FAIL — `AttributeError: 'BBIKDJSelector' object has no attribute 'warmup'`.

- [ ] **Step 3: Rewrite the selector**

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_ma, compute_dif
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class BBIKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        bbi_series = compute_bbi(market_data)
        dif_series = compute_dif(market_data)
        ma60_series = compute_ma(market_data, 60)
        short_line, long_line = compute_zx_lines(market_data)
        df = market_data.with_columns([
            j_series.alias("j"), bbi_series.alias("bbi"), dif_series.alias("dif"),
            ma60_series.alias("ma_60"), short_line.alias("short_term_trend_line"),
            long_line.alias("long_term_bull_bear_line"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        j_threshold = params.get("j_threshold", 15)
        bbi_min_window = params.get("bbi_min_window", 20)
        max_window = params.get("max_window", 120)
        bbi_q_threshold = params.get("bbi_q_threshold", 0.2)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            bbi_vals = hist["bbi"].drop_nulls()
            if len(bbi_vals) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["dif"] is None or latest["dif"] <= 0:
                continue
            if bbi_deriv_uptrend(bbi_vals, bbi_min_window, max_window, bbi_q_threshold):
                selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

Equivalence: original filtered candidates by last-row `j<15 & dif>0` then judged `bbi_deriv_uptrend` per candidate; the rewrite applies the identical per-code judgment to every code (same row(-1) checks, same bbi series logic) — same selected set.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/domain/test_selectors/ -q`
Expected: PASS (bbi_kdj_b1 tests + deterministic baseline with exact lists).

- [ ] **Step 5: Commit**

```bash
git add trendradar/domain/strategy/selectors/bbi_kdj_b1.py tests/domain/test_selectors/test_bbi_kdj_b1.py tests/domain/test_selectors/test_deterministic_baseline.py
git commit -m "perf: bbi_kdj_b1 two-phase (warmup/select_day)"
```

---

## Task 4: Refactor super_b1 selector

**Files:** `trendradar/domain/strategy/selectors/super_b1.py`, `tests/domain/test_selectors/test_super_b1.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Same test adaptation as Task 3 (drop get_data_dict, `_run` helper, baseline migrate). Verify RED (`no attribute 'warmup'`).

- [ ] **Step 3: Rewrite**

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj


class SuperB1Selector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        df = market_data.with_columns([j_series.alias("j")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        lookback_n = params.get("lookback_n", 10)
        close_vol_pct = params.get("close_vol_pct", 0.02)
        price_drop_pct = params.get("price_drop_pct", 0.02)
        j_threshold = params.get("j_threshold", 10)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < lookback_n + 1:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            prev = hist.row(-2, named=True)
            if prev["close"] is None or prev["close"] <= 0:
                continue
            price_drop = (latest["close"] - prev["close"]) / prev["close"]
            if price_drop > price_drop_pct:
                continue
            recent = hist.slice(-lookback_n, lookback_n)
            avg_vol = recent["volume"].mean()
            if avg_vol is None or avg_vol <= 0:
                continue
            if latest["volume"] / avg_vol < 1 - close_vol_pct:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** Verify GREEN + baseline exact lists. Commit `perf: super_b1 two-phase (warmup/select_day)`.

---

## Task 5: Refactor bbi_short_long selector

**Files:** `trendradar/domain/strategy/selectors/bbi_short_long.py`, `tests/domain/test_selectors/test_bbi_short_long.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (indicators: bbi + ma(n_short) + ma(n_long) in warmup; judgment identical):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.ma import compute_ma


class BBIShortLongSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        params = self.definition.default_params
        n_short = params.get("n_short", 5)
        n_long = params.get("n_long", 21)
        df = market_data.with_columns([
            compute_bbi(market_data).alias("bbi"),
            compute_ma(market_data, n_short).alias(f"ma_{n_short}"),
            compute_ma(market_data, n_long).alias(f"ma_{n_long}"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        n_short = params.get("n_short", 5)
        n_long = params.get("n_long", 21)
        bbi_min_window = params.get("bbi_min_window", 2)
        max_window = params.get("max_window", 120)
        ma_short_name = f"ma_{n_short}"
        ma_long_name = f"ma_{n_long}"

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["close"] is None or latest["close"] <= 0:
                continue
            if latest[ma_short_name] is None or latest[ma_long_name] is None:
                continue
            if latest[ma_short_name] <= latest[ma_long_name]:
                continue
            bbi_vals = hist["bbi"].drop_nulls()
            if len(bbi_vals) < max_window:
                continue
            if not bbi_deriv_uptrend(bbi_vals, bbi_min_window, max_window):
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: bbi_short_long two-phase (warmup/select_day)`.

---

## Task 6: Refactor peak_kdj selector

**Files:** `trendradar/domain/strategy/selectors/peak_kdj.py`, `tests/domain/test_selectors/test_peak_kdj.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (indicator: kdj j; judgment: j<10, 120d high/low fluctuation, gap):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj


class PeakKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        df = market_data.with_columns([j_series.alias("j")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        j_threshold = params.get("j_threshold", 10)
        max_window = params.get("max_window", 120)
        fluc_threshold = params.get("fluc_threshold", 0.03)
        gap_threshold = params.get("gap_threshold", 0.2)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            recent = hist.slice(-max_window, max_window)
            high_max = recent["high"].max()
            low_min = recent["low"].min()
            if high_max is None or low_min is None or high_max <= 0:
                continue
            fluc = (high_max - low_min) / high_max
            if fluc < fluc_threshold:
                continue
            close_now = latest["close"]
            gap = (high_max - close_now) / high_max
            if gap < gap_threshold:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: peak_kdj two-phase (warmup/select_day)`.

---

## Task 7: Refactor ma60_volume_wave selector

**Files:** `trendradar/domain/strategy/selectors/ma60_volume_wave.py`, `tests/domain/test_selectors/test_ma60_volume_wave.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (indicators: kdj j + ma60; judgment: j<15, close>ma60, volume spike):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_ma


class MA60VolumeWaveSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        ma60_series = compute_ma(market_data, 60)
        df = market_data.with_columns([j_series.alias("j"), ma60_series.alias("ma_60")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        lookback_n = params.get("lookback_n", 25)
        vol_multiple = params.get("vol_multiple", 1.8)
        j_threshold = params.get("j_threshold", 15)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < lookback_n + 1:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["close"] is None or latest["ma_60"] is None:
                continue
            if latest["close"] <= latest["ma_60"]:
                continue
            recent = hist.slice(-lookback_n, lookback_n)
            avg_vol = recent["volume"].mean()
            if avg_vol is None or avg_vol <= 0:
                continue
            if latest["volume"] <= avg_vol * vol_multiple:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: ma60_volume_wave two-phase (warmup/select_day)`.

---

## Task 8: Refactor zxdkx_balance selector

**Files:** `trendradar/domain/strategy/selectors/zxdkx_balance.py`, `tests/domain/test_selectors/test_zxdkx_balance.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (indicators: zx lines; judgment: close vs long line, zx stick condition on the code's series):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.zxdkx import (
    compute_zx_lines, zx_stick_condition,
)


class ZXDKXBalanceSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        short_line, long_line = compute_zx_lines(market_data)
        df = market_data.with_columns([
            short_line.alias("short_term_trend_line"),
            long_line.alias("long_term_bull_bear_line"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        zx_stick_limit_threshold = params.get("zx_stick_limit_threshold", 0.04)
        close_vs_long_term_bull_bear_line_limit_threshold = params.get(
            "close_vs_long_term_bull_bear_line_limit_threshold", 0.95
        )

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 114:
                continue
            latest = hist.row(-1, named=True)
            long_line_val = latest["long_term_bull_bear_line"]
            if long_line_val is None or long_line_val <= 0:
                continue
            close_to_long = latest["close"] / long_line_val
            if close_to_long > close_vs_long_term_bull_bear_line_limit_threshold:
                continue
            stick_cond_series = zx_stick_condition(
                hist["short_term_trend_line"],
                hist["long_term_bull_bear_line"],
                zx_stick_limit_threshold,
            )
            if len(stick_cond_series) == 0 or stick_cond_series[-1] is None:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: zxdkx_balance two-phase (warmup/select_day)`.

---

## Task 9: Refactor perfect_b1 selector

**Files:** `trendradar/domain/strategy/selectors/perfect_b1.py`, `tests/domain/test_selectors/test_perfect_b1.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (indicator: kdj j; judgment: j<13, amplitude, pct change):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj


class PerfectB1Selector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        df = market_data.with_columns([j_series.alias("j")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        j_threshold = params.get("j_threshold", 13)
        amplitude_limit = params.get("amplitude_limit", 0.07)
        pct_chg_upper = params.get("pct_chg_upper", 0.02)
        pct_chg_lower = params.get("pct_chg_lower", -0.02)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 2:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["high"] is None or latest["close"] is None or latest["close"] <= 0:
                continue
            amplitude = (latest["high"] - latest["low"]) / latest["close"]
            if amplitude > amplitude_limit:
                continue
            prev = hist.row(-2, named=True)
            if prev["close"] is None or prev["close"] <= 0:
                continue
            pct_chg = latest["close"] / prev["close"] - 1
            if pct_chg > pct_chg_upper or pct_chg < pct_chg_lower:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: perfect_b1 two-phase (warmup/select_day)`.

---

## Task 10: Refactor big_bullish_volume selector (no-indicator)

**Files:** `trendradar/domain/strategy/selectors/big_bullish_volume.py`, `tests/domain/test_selectors/test_big_bullish_volume.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Test adaptation (RED).

- [ ] **Step 3: Rewrite** (no indicators — warmup only groups raw OHLCV):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class BigBullishVolumeSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        grouped = {g["code"][0]: g for g in market_data.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        up_pct_threshold = params.get("up_pct_threshold", 0.06)
        upper_wick_pct_max = params.get("upper_wick_pct_max", 0.02)
        vol_multiple = params.get("vol_multiple", 2.5)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 2:
                continue
            latest = hist.row(-1, named=True)
            if latest["open"] is None or latest["close"] is None or latest["high"] is None:
                continue
            if latest["open"] <= 0 or latest["close"] <= 0 or latest["high"] <= 0:
                continue
            up_pct = (latest["close"] - latest["open"]) / latest["open"]
            if up_pct < up_pct_threshold:
                continue
            upper_wick = latest["high"] - latest["close"]
            upper_wick_pct = upper_wick / latest["close"]
            if upper_wick_pct > upper_wick_pct_max:
                continue
            prev = hist.row(-2, named=True)
            if prev["volume"] is None or prev["volume"] <= 0:
                continue
            if latest["volume"] / prev["volume"] < vol_multiple:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

- [ ] **Step 4-5:** GREEN + commit `perf: big_bullish_volume two-phase (warmup/select_day)`.

---

## Task 11: Refactor volume_spike_balance selector

**Files:** `trendradar/domain/strategy/selectors/volume_spike_balance.py`, `tests/domain/test_selectors/test_volume_spike_balance.py`, `test_deterministic_baseline.py`

- [ ] **Step 1-2:** Read the current file (has a spike-scan loop), adapt tests (RED).

- [ ] **Step 3: Rewrite** (indicator: zx lines; judgment: find volume spike in lookback, check close below long line on latest):

```python
import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class VolumeSpikeBalanceSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        short_line, long_line = compute_zx_lines(market_data)
        df = market_data.with_columns([
            short_line.alias("short_term_trend_line"),
            long_line.alias("long_term_bull_bear_line"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        volume_spike_lookback = params.get("volume_spike_lookback", 30)
        volume_spike_multiple = params.get("volume_spike_multiple", 2.0)
        min_spike_elapsed_days = params.get("min_spike_elapsed_days", 20)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < volume_spike_lookback + min_spike_elapsed_days + 2:
                continue
            latest = hist.row(-1, named=True)
            long_line_val = latest["long_term_bull_bear_line"]
            if long_line_val is None or long_line_val <= 0:
                continue
            if latest["close"] is None or latest["close"] >= long_line_val:
                continue

            # Spike scan identical to original: find day where volume >=
            # multiple * prior-day volume within lookback, with elapsed gap.
            rows = hist.to_dicts()
            found = False
            for i in range(len(rows) - min_spike_elapsed_days - 1, len(rows) - min_spike_elapsed_days):
                pass  # (replace with the original scan loop verbatim)
            # NOTE: copy the exact spike-scan loop from the original select()
            # body here; the rewrite only changes data access (hist from
            # grouped instead of df.filter(code==x)), never the scan logic.

            selected.append(code)  # only when found (see original)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
```

⚠️ **This task requires reading the original `volume_spike_balance.py` in full and porting its spike-scan loop verbatim into `select_day`** (same row iteration, same conditions, same `selected.append` gating). The placeholder loop above is NOT final — replace with the exact original logic. Do not invent a different spike rule.

- [ ] **Step 4-5:** GREEN (baseline exact lists must match) + commit `perf: volume_spike_balance two-phase (warmup/select_day)`.

---

## Task 12: Runner refactor (`_run_selection`)

**Files:**
- Modify: `trendradar/app/services/selection_service.py`
- Test: `tests/app/test_execution_registration.py`, `tests/app/test_selection_cancel.py` (adapt), `tests/app/test_market_service_incremental.py`

- [ ] **Step 1: Adapt the cancel-test slow selector (RED first)**

`tests/app/test_selection_cancel.py` `SlowSelector` → two-phase:

```python
class SlowSelector:
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data):
        from trendradar.domain.strategy.protocol import WarmupResult
        grouped = {g["code"][0]: g for g in market_data.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context, warmup):
        time.sleep(3)  # simulate long computation
        from trendradar.domain.strategy.protocol import SelectionResult
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=[],
        )
```

Run `tests/app/test_selection_cancel.py -q` → FAIL (runner still calls `.select`).

- [ ] **Step 2: Rewrite `_run_selection` per spec v9**

Replace the daily strategy loop with warmup-then-daily:

```python
def _run_selection(ctx, market_store, request, store) -> SignalSet:
    ctx.log("Resolving strategy groups and settings")
    group_defs, member_defs, settings_map = _get_strategy_resolve_input(store)
    resolved = resolve_strategies(group_defs, member_defs, settings_map, request)
    if not resolved:
        ctx.log("No strategies resolved")
        return SignalSet(execution_key=ctx.job_id)
    ctx.log(f"Resolved {len(resolved)} strategies: {[d.strategy_id for d in resolved]}")

    start_str = request.get("start_date")
    end_str = request.get("end_date")
    if start_str:
        start = date.fromisoformat(start_str)
    else:
        start = date.today() - timedelta(days=90)
    if end_str:
        end = date.fromisoformat(end_str)
    else:
        end = date.today()

    trading_dates = market_store.trading_dates(start, end)
    if not trading_dates:
        ctx.log("No trading dates found in range")
        return SignalSet(execution_key=ctx.job_id)
    ctx.log(f"Processing {len(trading_dates)} trading dates from {trading_dates[0]} to {trading_dates[-1]}")

    max_window = 120
    extended_start = trading_dates[0] - timedelta(days=max_window * 2)
    codes = request.get("codes")
    if codes is None or len(codes) == 0:
        meta = market_store.stock_meta()
        codes = meta["code"].to_list() if not meta.is_empty() else []
        ctx.log(f"Using all {len(codes)} available stocks")
    else:
        ctx.log(f"Using {len(codes)} specified stocks")
    codes = sorted(codes)   # normalize ordering (spec: deterministic across inputs)

    market_data = market_store.load_bars(codes, extended_start, trading_dates[-1])
    if market_data.is_empty():
        ctx.log("No market data loaded")
        return SignalSet(execution_key=ctx.job_id)
    market_data = market_data.sort(["code", "date"])   # ordering responsibility (runner)

    ctx.log(f"Loaded {market_data.height} market data rows")

    # Phase 1: warmup once per strategy
    warmups: dict[str, WarmupResult | None] = {}
    selectors: dict[str, SelectionStrategy] = {}
    for defn in resolved:
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return SignalSet(execution_key=ctx.job_id)
        ctx.log(f"Warmup {defn.strategy_id}")
        selector = defn.selector_class(defn)
        selectors[defn.strategy_id] = selector
        try:
            warmups[defn.strategy_id] = selector.warmup(market_data)
        except Exception as e:
            ctx.log(f"Error in warmup {defn.strategy_id}: {e}", level="WARN")
            warmups[defn.strategy_id] = None
        ctx.log(f"Warmup done {defn.strategy_id}")

    # Phase 2: per-day select_day
    all_signals: list[StrategySignal] = []
    total_dates = len(trading_dates)
    strategies_snapshot = [
        {"strategy_id": d.strategy_id, "name": d.name,
         "params": settings_map.get(d.strategy_id, {}).get("params", {})}
        for d in resolved
    ]

    for date_idx, trade_date in enumerate(trading_dates):
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return SignalSet(execution_key=ctx.job_id)
        ctx.update_progress(date_idx + 1, total_dates, str(trade_date))

        day_data = market_data.filter(pl.col("date") == trade_date)
        if day_data.is_empty():
            continue
        day_codes = day_data["code"].unique().to_list()
        candidate_codes = [c for c in codes if c in day_codes]
        if not candidate_codes:
            continue
        context = SelectionContext(
            trade_date=trade_date,
            market_data=market_data,
            candidate_codes=candidate_codes,
        )

        for defn in resolved:
            if ctx.check_cancelled():
                ctx.fail("Cancelled by user")
                return SignalSet(execution_key=ctx.job_id)
            if warmups.get(defn.strategy_id) is None:
                continue
            try:
                selector = selectors[defn.strategy_id]
                result = selector.select_day(context, warmups[defn.strategy_id])
                if result.selected_codes:
                    all_signals.append(StrategySignal(
                        strategy_id=result.strategy_id,
                        strategy_name=result.strategy_name,
                        signal_date=result.trade_date,
                        codes=result.selected_codes,
                    ))
            except Exception as e:
                ctx.log(f"Error in strategy {defn.strategy_id} on {trade_date}: {e}", level="WARN")

    ctx.log(f"Selection complete: {len(all_signals)} signals generated")
    return SignalSet(
        execution_key=ctx.job_id,
        signal_from=trading_dates[0],
        signal_to=trading_dates[-1],
        strategies_snapshot=strategies_snapshot,
        signals=all_signals,
    )
```

Add imports: `WarmupResult`, `SelectionStrategy` from `trendradar.domain.strategy.protocol`.

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/app/test_selection_cancel.py tests/app/test_execution_registration.py tests/domain/test_selectors/ -q`
Expected: PASS (cancel still fires <4.5s via per-strategy check; registration metadata unchanged; selectors + baseline green).

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add trendradar/app/services/selection_service.py tests/app/test_selection_cancel.py
git commit -m "perf: runner warmup-then-select_day orchestration"
```

---

## Task 13: Integration verification (real full-market timing)

**Files:**
- Test: `tests/app/test_selection_perf_smoke.py` (optional loose timing guard)

- [ ] **Step 1: Restart the API and run a real single-day full-market selection**

Run: `curl -X POST http://127.0.0.1:8002/api/executions -H 'Content-Type: application/json' -d '{"start_date":"2026-08-20","end_date":"2026-08-20","groups":["default"]}'`
Expected: job completes in **< 30 s** (was ~45 min). Verify signals via `/api/selection-results/{key}` and compare selected counts to the pre-optimization run (`aa49ffb5`: 10 strategies, 12538 total picks) — counts should be equal (behavior equivalence on real data).

- [ ] **Step 2: Optional smoke test (loose guard)**

```python
# tests/app/test_selection_perf_smoke.py
def test_small_selection_completes_quickly(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    import time
    from trendradar.app.jobs.executor import JobExecutor
    from trendradar.app.jobs.persistence import JobStore
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    from trendradar.domain.strategy.selectors import register_all
    register_all()

    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    class TinyMarket:
        def trading_dates(self, start, end):
            from datetime import date
            return [date(2026, 8, 20)]
        def load_bars(self, codes, start, end, columns=None):
            import polars as pl
            from datetime import date
            rows = []
            for i, c in enumerate(codes[:50]):
                for d in range(120):
                    rows.append({"code": c, "date": date(2026, 8, 20) - __import__("datetime").timedelta(days=120 - d),
                                 "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0 + d * 0.001,
                                 "volume": 1000000.0})
            return pl.DataFrame(rows)
        def stock_meta(self, codes=None):
            import polars as pl
            return pl.DataFrame({"code": [f"{i:06d}" for i in range(50)]})

    from trendradar.app.services.selection_service import submit_selection
    t0 = time.time()
    job = submit_selection(ex, TinyMarket(), {"start_date": "2026-08-20", "end_date": "2026-08-20"}, sc)
    ex._jobs[job].future.result(timeout=30)
    assert ex.get_state(job)["status"] == "success"
    assert time.time() - t0 < 30
    ex.shutdown(wait=True)
```

- [ ] **Step 3: Commit**

```bash
git add tests/app/test_selection_perf_smoke.py
git commit -m "test: selection perf smoke (loose 30s guard)"
```

---

## Self-Review

1. **Spec coverage:** warmup/select_day protocol (Task 2), all 9 selectors (Tasks 3-11, 8 with indicators + big_bullish_volume without), runner orchestration with cancel/progress/exception semantics (Task 12), behavior-equivalence baseline (Task 1), integration timing (Task 13). Memory/ordering/partition_by all per spec v9.
2. **Placeholder scan:** Task 11 has a marked placeholder that the executor MUST replace with the original spike-scan loop verbatim (explicitly flagged); every other task has full code.
3. **Type consistency:** `warmup(market_data) -> WarmupResult`, `select_day(context, warmup) -> SelectionResult` consistent across all tasks; `WarmupResult.grouped: dict[str, pl.DataFrame]`; runner uses `selectors[defn.strategy_id]` and `warmups[defn.strategy_id]` consistently.
4. **Equivalence constraint:** all select_day implementations judge `hist.row(-1)` (data end-day), label with `context.trade_date` — matching existing behavior; deterministic baseline (Task 1) is the hard proof gate.
