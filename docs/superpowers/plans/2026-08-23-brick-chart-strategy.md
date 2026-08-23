# 砖型图 策略 + compute_zx_lines 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `compute_zx_lines` (review #1: long line must use all four MAs) and add the 「砖型图」strategy (TDX MT-oscillator formula, C3 reuses the fixed `compute_zx_lines` long line as DKK).

**Architecture:** `compute_zx_lines` fix (one line + value-assertion tests); `compute_mt` pure per-code function (TDX SMA recursion via `ewm_mean(alpha=1/N, adjust=False)`); `BrickChartSelector` warmup = `compute_zx_lines` long line (DKK) + `compute_mt`, select_day judges last row (needs 5 MT values + MA114).

**Tech Stack:** Python 3.11+, polars.

**Spec:** `docs/superpowers/specs/2026-08-23-brick-chart-strategy-design.md`

**Baseline:** `cd /root/workspace/repo/GunArk && python3 -m pytest -q -p no:cacheprovider` → 278 passed.

---

### Task 1: Fix `compute_zx_lines` long-line formula (review #1)

**Files:**
- Modify: `trendradar/domain/strategy/formulas/zxdkx.py`
- Test: `tests/domain/test_formulas/test_zxdkx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_compute_zx_lines_long_line_is_four_ma_average():
    # 200 个常量收盘价：四条均线都应等于收盘价
    df = pl.DataFrame({"close": [10.0] * 200})
    short_line, long_line = compute_zx_lines(df)
    assert short_line[-1] == 10.0
    assert long_line[-1] == 10.0  # 修复前：long_line = (2*ma57+2*ma114)/4，常量下仍=10，需另一断言


def test_compute_zx_lines_long_line_uses_ma14_and_ma28():
    # 线性递增：ma14 > ma28 > ma57 > ma114，四线平均 < 只取长线两条的均值
    df = pl.DataFrame({"close": [float(i) for i in range(1, 201)]})
    _, long_line = compute_zx_lines(df)
    ma14 = sum(range(187, 201)) / 14
    ma28 = sum(range(173, 201)) / 28
    ma57 = sum(range(144, 201)) / 57
    ma114 = sum(range(87, 201)) / 114
    assert long_line[-1] is not None
    assert abs(long_line[-1] - (ma14 + ma28 + ma57 + ma114) / 4.0) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_formulas/test_zxdkx.py -q -p no:cacheprovider`
Expected: FAIL — `long_line[-1] == (ma14+ma28+ma57+ma114)/4` mismatch (current = (ma57+ma114)/2).

- [ ] **Step 3: Implement**

`zxdkx.py`:

```python
def compute_zx_lines(df, m1=14, m2=28, m3=57, m4=114):
    close = df["close"]
    ma1 = close.rolling_mean(m1)
    ma2 = close.rolling_mean(m2)
    ma3 = close.rolling_mean(m3)
    ma4 = close.rolling_mean(m4)
    long_line = (ma1 + ma2 + ma3 + ma4) / 4
    return ma1.alias("short_term_trend_line"), long_line.alias("long_term_bull_bear_line")
```

- [ ] **Step 4: Run test to verify it passes + baseline impact check**

Run: `python3 -m pytest tests/domain/test_formulas/test_zxdkx.py tests/domain/test_selectors/test_deterministic_baseline.py -q -p no:cacheprovider`
Expected: PASS — baseline stays `[]` for zxdkx_balance/volume_spike_balance (uptrend fixtures keep excluding; verify; if changed, re-capture `_EXPECTED`).

- [ ] **Step 5: Full suite + commit**

```bash
python3 -m pytest -q -p no:cacheprovider   # → 280 passed
git add trendradar/domain/strategy/formulas/zxdkx.py tests/domain/test_formulas/test_zxdkx.py
git commit -m "fix: compute_zx_lines long line uses all four MAs (review #1); value-assertion tests"
```

### Task 2: `compute_mt` pure function (TDD, reference-verified)

**Files:**
- Create: `trendradar/domain/strategy/formulas/brick.py` (or inline in selector — spec says selector file; put `compute_mt` in `selectors/brick_chart.py` top-level for import by tests)
- Test: `tests/domain/test_selectors/test_brick_chart.py`

- [ ] **Step 1: Write the failing test (reference implementation cross-check)**

```python
"""compute_mt: TDX SMA chain verified against a plain Python reference loop."""
import polars as pl
import random
from trendradar.domain.strategy.selectors.brick_chart import compute_mt


def _sma_ref(xs, n, m=1):
    """TDX SMA(X,N,M) = (M*X + (N-M)*Y_prev)/N; first value = X."""
    y = None
    out = []
    for x in xs:
        if y is None:
            y = x
        else:
            y = (m * x + (n - m) * y) / n
        out.append(y)
    return out


def _mt_ref(highs, lows, closes, n=4, m=6, t=4):
    hh = [max(highs[max(0, i - n + 1):i + 1]) for i in range(len(highs))]
    ll = [min(lows[max(0, i - n + 1):i + 1]) for i in range(len(lows))]
    var1 = [(hh[i] - closes[i]) / (hh[i] - ll[i]) * 100 - 90 if hh[i] != ll[i] else float("nan") for i in range(len(closes))]
    var2 = [v + 100 for v in _sma_ref(var1, n, 1)]
    var3 = [(closes[i] - ll[i]) / (hh[i] - ll[i]) * 100 if hh[i] != ll[i] else float("nan") for i in range(len(closes))]
    var4 = _sma_ref(var3, m, 1)
    var5 = [v + 100 for v in _sma_ref(var4, m, 1)]
    var6 = [var5[i] - var2[i] for i in range(len(closes))]
    return [max(v6 - t, 0.0) for v6 in var6]


def test_compute_mt_matches_reference():
    random.seed(7)
    n = 40
    closes = [10 + i * 0.3 for i in range(n)]
    highs = [c * 1.03 for c in closes]
    lows = [c * 0.97 for c in closes]
    got = compute_mt(pl.Series(highs), pl.Series(lows), pl.Series(closes))
    exp = _mt_ref(highs, lows, closes)
    got_list = got.to_list()
    assert len(got_list) == n
    for i in range(n):
        g, e = got_list[i], exp[i]
        if e != e:  # NaN
            assert g != g or g is None
        else:
            assert abs(g - e) < 1e-6
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/domain/test_selectors/test_brick_chart.py::test_compute_mt_matches_reference -q -p no:cacheprovider`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement `compute_mt`**

In `selectors/brick_chart.py`:

```python
def compute_mt(high: pl.Series, low: pl.Series, close: pl.Series,
               n: int = 4, m: int = 6, t: int = 4) -> pl.Series:
    """TDX 砖型图 MT 振荡器（SMA(X,N,1) = ewm(alpha=1/N)）。"""
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

(Note: polars ewm skips NaN inputs; leading NaN from rolling windows only affects early rows, matching the reference which carries NaN through SMA — if the reference test exposes a NaN-vs-number difference, adjust the reference to match polars semantics and note it.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/domain/test_selectors/test_brick_chart.py -q -p no:cacheprovider`
Expected: PASS

### Task 3: `BrickChartSelector` (TDD)

**Files:**
- Modify: `selectors/brick_chart.py` (add selector class)
- Modify: `selectors/__init__.py` (register)
- Modify: `tests/interfaces/test_api_contract.py:234` (10 → 11)
- Test: `tests/domain/test_selectors/test_brick_chart.py`

- [ ] **Step 1: Write decision tests (crafted mt/dkk columns)**

```python
from trendradar.domain.strategy.protocol import SelectionContext, WarmupResult
from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.selectors.brick_chart import BrickChartSelector


def _defn():
    return StrategyDefinition(strategy_id="brick_chart", name="砖型图", description="",
                              selector_class=BrickChartSelector, default_params={})


def _hist(mt_vals, close=120.0, dkk=100.0):
    n = len(mt_vals)
    return pl.DataFrame({
        "code": ["000001"] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "mt": [float(v) for v in mt_vals],
        "close": [close] * n,
        "dkk": [dkk] * n,
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n, "volume": [1e6] * n,
    })


def _run(hist):
    warmup = WarmupResult(grouped={"000001": hist})
    ctx = SelectionContext(trade_date=hist["date"][-1], market_data=hist,
                           candidate_codes=["000001"])
    return BrickChartSelector(_defn()).select_day(ctx, warmup).selected_codes


def test_selected_when_red_after_3_green_with_enough_height():
    # 最近 5 个 MT: [8, 7, 6, 5, 8] → t-4..t: 8,7,6,5,8
    # red=8>5, green1=5<6, green2=6<7, green3=7<8, red_h=3 >= green_h1=1 → 选中
    assert _run(_hist([8, 7, 6, 5, 8])) == ["000001"]


def test_excluded_when_mt_not_rising():
    # 最近 5 个: [8, 7, 6, 5, 4] → red=4>5 失败 → 排除
    assert _run(_hist([8, 7, 6, 5, 4])) == []


def test_excluded_when_green_chain_broken():
    # [8, 7, 9, 5, 8] → green2 = 9<7 失败 → 排除
    assert _run(_hist([8, 7, 9, 5, 8])) == []


def test_excluded_when_rise_smaller_than_prior_fall():
    # [8, 7, 6, 5, 5.5] → red_h=0.5 < green_h1=1 → 排除
    assert _run(_hist([8, 7, 6, 5, 5.5])) == []


def test_excluded_when_close_below_dkk():
    # 形态满足但 close(100) < dkk(120) → 排除
    assert _run(_hist([8, 7, 6, 5, 8], close=100.0, dkk=120.0)) == []


def test_short_history_excluded():
    # 不足 5 个 MT 值 → 排除
    assert _run(_hist([5, 6, 5, 8])) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/domain/test_selectors/test_brick_chart.py -q -p no:cacheprovider`
Expected: FAIL (import error / empty selection)

- [ ] **Step 3: Implement `BrickChartSelector`**

In `selectors/brick_chart.py` (alongside `compute_mt`):

```python
class BrickChartSelector(SelectionStrategy):
    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines
        _, long_line = compute_zx_lines(market_data)  # 四线平均 = DKK
        mt = compute_mt(market_data["high"], market_data["low"], market_data["close"])
        df = market_data.with_columns([long_line.alias("dkk"), mt])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context, warmup):
        t0 = time.time()
        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 115:   # MA114 数据不足
                continue
            mt = hist["mt"]
            last5 = [mt[-1], mt[-2], mt[-3], mt[-4], mt[-5]]
            if any(v is None for v in last5):
                continue
            mt_t, mt_1, mt_2, mt_3, mt_4 = last5
            red = mt_t > mt_1
            green1 = mt_1 < mt_2
            green2 = mt_2 < mt_3
            green3 = mt_3 < mt_4
            red_h = mt_t - mt_1
            green_h1 = mt_1 - mt_2
            c1 = red and green1 and red_h >= green_h1
            c2 = green1 and green2 and green3
            latest = hist.row(-1, named=True)
            c3 = latest["close"] >= latest["dkk"]
            if c1 and c2 and c3:
                selected.append(code)
        return SelectionResult(strategy_id=self.definition.strategy_id,
                               strategy_name=self.definition.name,
                               trade_date=context.trade_date,
                               selected_codes=selected,
                               elapsed_seconds=time.time() - t0)
```

- [ ] **Step 4: Register + contract count**

`selectors/__init__.py`: import + register `brick_chart` (name 砖型图, description "MT 振荡器转升 + 前 3 日绿柱 + 收盘站上四线均值", default_params `{"n": 4, "m": 6, "t": 4, "m1": 14, "m2": 28, "m3": 57, "m4": 114}`).
`test_api_contract.py:234`: `== 10` → `== 11`.

- [ ] **Step 5: Full suite + commit**

```bash
python3 -m pytest -q -p no:cacheprovider   # → 287 passed
git add trendradar/domain/strategy/selectors/ tests/
git commit -m "feat: 砖型图 selection strategy (MT oscillator + DKK via compute_zx_lines)"
```

### Task 4: Deployment verification

- [ ] `./scripts/restart.sh`
- [ ] `curl -s http://localhost:8818/api/strategies` contains 砖型图 (11 strategies)
- [ ] Optional: run a real selection with 砖型图 and confirm completion

---

## Sequencing

Task 1 (zxdkx fix) must precede Task 3 (brick selector uses the fixed function). Task 2 (compute_mt) independent of 1. Tasks 2/3 share the brick_chart.py file — do 2 then 3 sequentially. Every task ends green; commit after each.

## Spec coverage

| Spec | Task |
|---|---|
| §2 SMA→ewm mapping / MT chain | 2 |
| §3.1 compute_mt decomposition | 2 |
| §3.1 selector C1/C2/C3 + len guard | 3 |
| DKK via compute_zx_lines (user request) | 1 (fix) + 3 (reuse) |
| §4 test plan | 1, 2, 3 |
