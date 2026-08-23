# 超跌抄底 策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 「超跌抄底」strategy (TDX: MT turn-up + DD2 second-diff new-high + below ZXK/DKK + MACD DIF turn-up), reusing the MT oscillator (extracted to `formulas/`) and baking in all prior review fixes (per-code ewm/REF, NaN/null guards, suspension handled by the runner).

**Architecture:** `compute_mt` moves to `formulas/mt_oscillator.py` (brick_chart imports it); new selector computes MT/ZXK/DIFF/DD2 per-code, DKK via `compute_zx_lines`; select_day guards every judged value against null/NaN and checks C1-C5.

**Tech Stack:** Python 3.11+, polars (ewm_mean adjust=False for TDX SMA/EMA; shift for REF).

**Spec:** `docs/superpowers/specs/2026-08-23-oversold-bottom-fishing-design.md`

**Baseline:** `python3 -m pytest -q -p no:cacheprovider` → 290 passed.

---

### Task 1: Extract `compute_mt` to `formulas/mt_oscillator.py` (refactor, tests stay green)

**Files:**
- Create: `trendradar/domain/strategy/formulas/mt_oscillator.py`
- Modify: `selectors/brick_chart.py` (import from formulas)
- Test: existing `tests/domain/test_selectors/test_brick_chart.py` (unchanged — must still pass)

- [ ] **Step 1: Create `formulas/mt_oscillator.py`** — move `compute_mt` verbatim from `brick_chart.py`:

```python
"""TDX 砖型图/超跌抄底 MT 振荡器（SMA(X,N,1) = ewm(alpha=1/N)）。"""

from __future__ import annotations

import polars as pl


def compute_mt(high: pl.Series, low: pl.Series, close: pl.Series,
               n: int = 4, m: int = 6, t: int = 4) -> pl.Series:
    """TDX MT 振荡器: max(EMA链差值 - t, 0)。"""
    hh = high.rolling_max(n)
    ll = low.rolling_min(n)
    span = hh - ll
    var1 = (hh - close) / span * 100 - 90
    var2 = var1.ewm_mean(alpha=1.0 / n, adjust=False) + 100
    var3 = (close - ll) / span * 100
    var4 = var3.ewm_mean(alpha=1.0 / m, adjust=False)
    var5 = var4.ewm_mean(alpha=1.0 / m, adjust=False) + 100
    var6 = var5 - var2
    return (var6 - t).clip(lower_bound=0.0).alias("mt")
```

- [ ] **Step 2: Update `brick_chart.py`** — remove the local `compute_mt` def; import `from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt`.

- [ ] **Step 3: Run brick + reference tests**

Run: `python3 -m pytest tests/domain/test_selectors/test_brick_chart.py -q -p no:cacheprovider`
Expected: PASS (all 7, including `test_compute_mt_matches_reference` and `test_warmup_mt_computed_per_code`).

- [ ] **Step 4: Full suite + commit**

```bash
python3 -m pytest -q -p no:cacheprovider   # → 290 passed
git add trendradar/domain/strategy/formulas/mt_oscillator.py trendradar/domain/strategy/selectors/brick_chart.py
git commit -m "refactor: extract compute_mt to formulas/mt_oscillator (shared by brick & oversold)"
```

### Task 2: `OversoldBottomFishingSelector` (TDD, reference-verified)

**Files:**
- Create: `selectors/oversold_bottom_fishing.py`
- Test: `tests/domain/test_selectors/test_oversold_bottom_fishing.py`

- [ ] **Step 1: Write reference cross-validation test**

```python
"""超跌抄底: 完整公式链参考实现逐值交叉验证 + 决策用例。"""
from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.protocol import SelectionContext, WarmupResult
from trendradar.domain.strategy.selectors.oversold_bottom_fishing import (
    OversoldBottomFishingSelector,
)


def _sma_ref(xs, n, m=1):
    y = None
    out = []
    for x in xs:
        if x is None or x != x:
            out.append(x)
            continue
        if y is None:
            y = x
        else:
            y = (m * x + (n - m) * y) / n
        out.append(y)
    return out


def _ewma_ref(xs, alpha):
    y = None
    out = []
    for x in xs:
        if x is None or x != x:
            out.append(x)
            continue
        if y is None:
            y = x
        else:
            y = alpha * x + (1 - alpha) * y
        out.append(y)
    return out


def _ref(highs, lows, closes, n=4, m=6, t=4):
    """返回 (mt, zxk, diff, dd2) 参考序列（含 None 语义）。"""
    hh = [None if i < n - 1 else max(highs[i - n + 1:i + 1]) for i in range(len(highs))]
    ll = [None if i < n - 1 else min(lows[i - n + 1:i + 1]) for i in range(len(lows))]
    var1 = [None if hh[i] is None or hh[i] == ll[i] else (hh[i] - closes[i]) / (hh[i] - ll[i]) * 100 - 90
            for i in range(len(closes))]
    var2 = [v + 100 if v is not None else None for v in _sma_ref(var1, n, 1)]
    var3 = [None if hh[i] is None or hh[i] == ll[i] else (closes[i] - ll[i]) / (hh[i] - ll[i]) * 100
            for i in range(len(closes))]
    var4 = _sma_ref(var3, m, 1)
    var5 = [v + 100 if v is not None else None for v in _sma_ref(var4, m, 1)]
    var6 = [var5[i] - var2[i] if var5[i] is not None and var2[i] is not None else None
            for i in range(len(closes))]
    mt = [None if v6 is None else max(v6 - t, 0.0) for v6 in var6]
    zxk = _ewma_ref(_ewma_ref(closes, 2 / 11), 2 / 11)
    diff = [a - b if a is not None and b is not None else None
            for a, b in zip(_ewma_ref(closes, 2 / 13), _ewma_ref(closes, 2 / 27))]
    dd2 = [None if i < 2 else closes[i] - 2 * closes[i - 1] + closes[i - 2] for i in range(len(closes))]
    return mt, zxk, diff, dd2


def _mkdf(code, closes, scale=1.03):
    n = len(closes)
    return pl.DataFrame({
        "code": [code] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "open": closes, "close": closes,
        "high": [c * scale for c in closes], "low": [c / scale for c in closes],
        "volume": [1e6] * n,
    })


def test_warmup_series_match_reference():
    n = 60
    closes = [10 + i * 0.3 for i in range(n)]
    df = _mkdf("000001", closes)
    defn = StrategyDefinition(strategy_id="oversold_bottom_fishing", name="", description="",
                              selector_class=OversoldBottomFishingSelector, default_params={})
    w = defn.selector_class(defn).warmup(df)
    g = w.grouped["000001"]
    mt_r, zxk_r, diff_r, dd2_r = _ref(
        [c * 1.03 for c in closes], [c / 1.03 for c in closes], closes)
    for col, exp in [("mt", mt_r), ("zxk", zxk_r), ("diff", diff_r), ("dd2", dd2_r)]:
        got = g[col].to_list()
        assert len(got) == len(exp)
        for i, (a, b) in enumerate(zip(got, exp)):
            if b is None:
                assert a is None
            else:
                assert a is not None and abs(a - b) < 1e-6, f"{col}[{i}]: {a} vs {b}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/domain/test_selectors/test_oversold_bottom_fishing.py::test_warmup_series_match_reference -q -p no:cacheprovider`
Expected: FAIL (import error)

- [ ] **Step 3: Implement the selector (per-code indicators + null/NaN guards)**

```python
"""超跌抄底: MT 转升 + 收盘二阶差分新高 + 双线下方超跌 + MACD DIF 拐头. ..."""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt


class OversoldBottomFishingSelector(SelectionStrategy):
    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines
        _, dkk = compute_zx_lines(market_data)  # 扁平列；判定行 MA114 窗口在股内
        parts = []
        for g in market_data.partition_by("code"):
            close = g["close"]
            mt = compute_mt(g["high"], g["low"], close)
            zxk = close.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
            diff = (close.ewm_mean(alpha=2 / 13, adjust=False)
                    - close.ewm_mean(alpha=2 / 27, adjust=False))
            dd2 = close - 2 * close.shift(1) + close.shift(2)
            parts.append(g.with_columns([
                mt, zxk.alias("zxk"), diff.alias("diff"), dd2.alias("dd2"),
            ]))
        df = pl.concat(parts).with_columns([dkk.alias("dkk")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 115:   # DKK 需 MA114
                continue
            latest = hist.row(-1, named=True)
            if any(v is None or v != v for v in
                   (latest["mt"], latest["zxk"], latest["diff"], latest["dd2"], latest["dkk"], latest["close"])):
                continue  # null/NaN 一律排除
            mt = hist["mt"].to_list()
            mt_t, mt_1, mt_2 = mt[-1], mt[-2], mt[-3]
            if not (mt_t > mt_1 and mt_1 < mt_2 and mt_t - mt_1 >= mt_2 - mt_1):
                continue  # C1
            dd2 = hist["dd2"].to_list()
            dd2_t = dd2[-1]
            if not (dd2_t > 0 and dd2_t == max(dd2[-5:])):
                continue  # C2
            if not (latest["zxk"] < latest["dkk"]):
                continue  # C3
            if not (latest["close"] < latest["zxk"]):
                continue  # C4
            diff = hist["diff"].to_list()
            if not (all(d < 0 for d in diff[-5:])          # EVERY(DIFF<0,5)
                    and diff[-1] >= diff[-2]                # 今日走平/回升
                    and diff[-2] < diff[-3] and diff[-3] < diff[-4]
                    and diff[-4] < diff[-5] and diff[-5] < diff[-6]):  # 前 4 日持续下行
                continue  # C5
            selected.append(code)
        return SelectionResult(strategy_id=self.definition.strategy_id,
                               strategy_name=self.definition.name,
                               trade_date=context.trade_date,
                               selected_codes=selected,
                               elapsed_seconds=time.time() - t0)
```

- [ ] **Step 4: Run reference test to verify it passes**

Run: `python3 -m pytest tests/domain/test_selectors/test_oversold_bottom_fishing.py -q -p no:cacheprovider`
Expected: PASS

### Task 3: Decision tests (crafted columns, C1-C5 isolation)

**Files:**
- Test: `tests/domain/test_selectors/test_oversold_bottom_fishing.py`

- [ ] **Step 1: Add decision tests**

```python
def _hist(mt5, dd2s, zxk, dkk, close, diffs):
    """构造满足/违反条件的 hist（长度 115+）。"""
    n = 120
    base = [0.0] * (n - 5)
    mt_col = base + [float(v) for v in mt5]
    dd2_col = base + [float(v) for v in dd2s]
    diff_col = base + [float(v) for v in diffs]
    return pl.DataFrame({
        "code": ["000001"] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "mt": mt_col, "dd2": dd2_col, "diff": diff_col,
        "zxk": [float(zxk)] * n, "dkk": [float(dkk)] * n, "close": [float(close)] * n,
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n, "volume": [1e6] * n,
    })


def _run(hist):
    w = WarmupResult(grouped={"000001": hist})
    ctx = SelectionContext(trade_date=hist["date"][-1], market_data=hist,
                           candidate_codes=["000001"])
    return OversoldBottomFishingSelector(_defn()).select_day(ctx, w).selected_codes


def test_all_conditions_selected():
    # mt=[8,7,6,5,8]: C1 ✅; dd2 最后5个 [.., -1, -0.5, 0.2, 1.5, 2.0] 今日最高>0: C2 ✅
    # zxk=90 < dkk=100: C3 ✅; close=80 < zxk=90: C4 ✅
    # diff 最后6个 [.., -2,-1.6,-1.2,-0.8,-0.4,-0.1]: 近5全负且逐日收窄(仍负) ✅
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, -0.1])
    assert _run(hist) == ["000001"]


def test_c1_fails_when_mt_not_rising():
    hist = _hist(mt5=[8, 7, 6, 5, 4], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, -0.1])
    assert _run(hist) == []


def test_c2_fails_when_dd2_not_new_high():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[3.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, -0.1])
    assert _run(hist) == []   # dd2[-5]=3.0 > 今日 2.0


def test_c3_fails_when_zxk_above_dkk():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=110.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, -0.1])
    assert _run(hist) == []


def test_c4_fails_when_close_above_zxk():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=95.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, -0.1])
    assert _run(hist) == []


def test_c5_fails_when_diff_not_negative_streak():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, 1.0, 0.1])  # 近5有正值
    assert _run(hist) == []


def test_nan_mt_excluded():
    # mt 含 NaN（0-span 场景）→ 排除
    hist = _hist(mt5=[8, 7, 6, 5, float("nan")], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, 0.1])
    assert _run(hist) == []


def test_short_history_excluded():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, -0.4, 0.1]).head(10)
    assert _run(hist) == []
```

- [ ] **Step 2: Run to verify they fail initially** (before Task 3 lands, only reference test exists — decision tests fail on import of `_defn` if absent; add `_defn` helper):

```python
def _defn():
    return StrategyDefinition(strategy_id="oversold_bottom_fishing", name="超跌抄底",
                              description="", selector_class=OversoldBottomFishingSelector,
                              default_params={})
```

Run: `python3 -m pytest tests/domain/test_selectors/test_oversold_bottom_fishing.py -q -p no:cacheprovider`
Expected: PASS after selector exists; run full suite.

- [ ] **Step 3: Register + contract count**

`selectors/__init__.py`: import + register `oversold_bottom_fishing` (name 超跌抄底, description "MT 转升 + 收盘二阶差分新高 + 双线下方超跌 + MACD DIF 拐头", default_params from spec §3.4).
`tests/interfaces/test_api_contract.py:234`: `== 11` → `== 12`.

- [ ] **Step 4: Full suite + commit**

```bash
python3 -m pytest -q -p no:cacheprovider   # → 297+ passed
git add trendradar/domain/strategy/selectors/ tests/domain/test_selectors/test_oversold_bottom_fishing.py tests/interfaces/test_api_contract.py
git commit -m "feat: 超跌抄底 selection strategy (oversold bottom-fishing)"
```

### Task 4: Deployment verification

- [ ] `./scripts/restart.sh`
- [ ] `curl -s http://localhost:8818/api/strategies` contains 超跌抄底 (12 strategies)
- [ ] Run a live selection with 超跌抄底; confirm completion; user compares with TDX

---

## Sequencing

Task 1 (extract) → Task 2 (selector + reference test) → Task 3 (decision tests + register). All sequential (same files). Commit after each.

## Spec coverage

| Spec | Task |
|---|---|
| §3.1 per-code indicators | 2 |
| §3.2 C1-C5 | 2, 3 |
| §3.3 guards (suspension/NaN/short) | 2, 3 (runner handles suspension) |
| §3.4 params / §3.5 register | 3 |
| §4 tests | 1, 2, 3 |
