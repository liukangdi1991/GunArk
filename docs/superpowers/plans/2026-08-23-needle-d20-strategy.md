# 单针下20 策略 + 按需流通市值 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 「单针下20」selection strategy (TDX formula: 3-day stochastic ≤ 20 AND 21-day stochastic > 80 AND float market cap ≥ 50亿) with on-demand per-day `daily_basic` market-cap fetching into an in-memory cache — no data-pipeline changes.

**Architecture:** `SelectionContext.market_cap` field (domain) + `REQUIRES_MARKET_CAP` selector capability + `daily_basic_circ_mv` infrastructure helper with a process-level per-date cache + selection_service wiring. Selector computes rolling stochastic indicators in warmup (house flat-column pattern) and reads market cap from context in select_day.

**Tech Stack:** Python 3.11+, polars, Tushare `daily_basic` (verified: token has access; `circ_mv` = 流通市值 in 万元).

**Spec:** `docs/superpowers/specs/2026-08-23-needle-d20-strategy-design.md`

**Baseline (must stay green):** `cd /root/workspace/repo/GunArk && python3 -m pytest -q -p no:cacheprovider` → 270 passed.

---

### Task 1: `SelectionContext.market_cap` field (TDD)

**Files:**
- Modify: `trendradar/domain/strategy/protocol.py`
- Test: `tests/domain/test_protocol_shape.py`

- [ ] **Step 1: Write the failing test** — append to `test_protocol_shape.py`:

```python
def test_selection_context_market_cap_field_defaults_none():
    import dataclasses
    fields = {f.name: f.default for f in dataclasses.fields(SelectionContext)}
    assert "market_cap" in fields
    assert fields["market_cap"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_protocol_shape.py -q -p no:cacheprovider`
Expected: FAIL (`market_cap` not in fields)

- [ ] **Step 3: Implement**

`trendradar/domain/strategy/protocol.py` — add field to `SelectionContext`:

```python
@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    candidate_codes: list[str] | None = None
    market_data: pl.DataFrame = field(default_factory=pl.DataFrame)
    market_cap: dict[str, float] | None = None   # 当日全市场 code→流通市值(万元)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/domain/test_protocol_shape.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trendradar/domain/strategy/protocol.py tests/domain/test_protocol_shape.py
git commit -m "feat: SelectionContext.market_cap field (on-demand float market cap)"
```

### Task 2: `daily_basic_circ_mv` infrastructure helper (TDD)

**Files:**
- Create: `trendradar/infrastructure/tushare/market_cap.py`
- Test: `tests/infrastructure/test_market_cap.py`

- [ ] **Step 1: Write the failing test**

```python
"""daily_basic_circ_mv: per-day full-market float market cap with in-memory cache."""
from datetime import date
from unittest.mock import MagicMock

from trendradar.infrastructure.tushare import market_cap as mc


def test_fetch_populates_and_caches(tmp_path):
    mc._MARKET_CAP_CACHE.clear()
    pro = MagicMock()
    resp = MagicMock()
    resp.to_dict.return_value = [
        {"ts_code": "000001.SZ", "circ_mv": 22141886.585},
        {"ts_code": "600519.SH", "circ_mv": 159114100.0},
        {"ts_code": "000002.SZ", "circ_mv": None},   # 缺失 → 跳过
    ]
    pro.daily_basic.return_value = resp

    result = mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert result == {"000001": 22141886.585, "600519": 159114100.0}
    assert pro.daily_basic.call_count == 1

    # cache hit: no second API call
    mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert pro.daily_basic.call_count == 1
    mc._MARKET_CAP_CACHE.clear()


def test_nan_circ_mv_skipped(tmp_path):
    mc._MARKET_CAP_CACHE.clear()
    pro = MagicMock()
    resp = MagicMock()
    resp.to_dict.return_value = [{"ts_code": "000001.SZ", "circ_mv": float("nan")}]
    pro.daily_basic.return_value = resp
    result = mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert result == {}
    mc._MARKET_CAP_CACHE.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_market_cap.py -q -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

`trendradar/infrastructure/tushare/market_cap.py`:

```python
"""On-demand float market cap (daily_basic.circ_mv, 万元) with in-memory cache.

Fetched per trade_date at selection time; dates are immutable facts, so the
process-level cache needs no TTL. circ_mv unit: 万元 (verified 2026-08-23).
"""

from __future__ import annotations

from datetime import date

_MARKET_CAP_CACHE: dict[date, dict[str, float]] = {}


def daily_basic_circ_mv(pro, trade_date: date) -> dict[str, float]:
    """Full-market float market cap for one trade day: {code: circ_mv_万元}."""
    if trade_date in _MARKET_CAP_CACHE:
        return _MARKET_CAP_CACHE[trade_date]
    resp = pro.daily_basic(
        trade_date=trade_date.strftime("%Y%m%d"),
        fields="ts_code,circ_mv",
    )
    result: dict[str, float] = {}
    for row in resp.to_dict(orient="records"):
        code = str(row["ts_code"])[:6]
        mv = row["circ_mv"]
        if mv is not None and mv == mv:  # skip NaN
            result[code] = float(mv)
    _MARKET_CAP_CACHE[trade_date] = result
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_market_cap.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/market_cap.py tests/infrastructure/test_market_cap.py
git commit -m "feat: daily_basic_circ_mv on-demand market cap with per-date cache"
```

### Task 3: 「单针下20」selector (TDD)

**Files:**
- Create: `trendradar/domain/strategy/selectors/single_needle_down_20.py`
- Modify: `trendradar/domain/strategy/selectors/__init__.py` (register)
- Modify: `tests/interfaces/test_api_contract.py:234` (strategies 9 → 10)
- Test: `tests/domain/test_selectors/test_single_needle_down_20.py`

- [ ] **Step 1: Write the failing test**

```python
"""单针下20: 3-day stochastic ≤ 20 AND 21-day stochastic > 80 AND circ_mv ≥ 50亿."""
from datetime import date

import polars as pl

from trendradar.domain.strategy.protocol import SelectionContext
from trendradar.domain.strategy.selectors.single_needle_down_20 import (
    SingleNeedleDown20Selector,
)
from trendradar.domain.strategy.models import StrategyDefinition


def _defn():
    return StrategyDefinition(
        strategy_id="single_needle_down_20", name="单针下20",
        description="", selector_class=SingleNeedleDown20Selector,
        default_params={"n1": 3, "n2": 21, "short_max": 20,
                        "long_min": 80, "circ_mv_min_yi": 50},
    )


def _run(df, market_cap=None, trade_date=date(2026, 8, 21)):
    sel = _defn().selector_class(_defn())
    warmup = sel.warmup(df)
    ctx = SelectionContext(trade_date=trade_date, market_data=df,
                           candidate_codes=df["code"].unique().to_list(),
                           market_cap=market_cap)
    return sel.select_day(ctx, warmup).selected_codes


def _top_stall_df(code="000001"):
    """21日上行到高点后顶部窄幅整理，今日收盘恰为3日最低（光脚收盘）。

    LLV(L,3)=119.9, HHV(C,3)=121, C=120 → 短期=100*(0.1/1.1)≈9.1 ≤20 ✅
    LLV(L,21)=89.1, HHV(C,21)=121, C=120 → 长期=100*(30.9/31.9)≈96.9 >80 ✅
    """
    from datetime import timedelta
    n = 25
    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(n)]
    closes = [90 + i for i in range(20)] + [120.0, 120.8, 121.0, 120.0]
    highs = [c * 1.01 for c in closes[:20]] + [121.2, 121.5, 121.8, 120.8]
    lows = [c * 0.99 for c in closes[:20]] + [119.5, 119.9, 120.5, 120.0]
    return pl.DataFrame({
        "code": [code] * n, "date": dates,
        "open": closes, "close": closes, "high": highs, "low": lows,
        "volume": [1e6] * n,
    })


def test_top_stall_close_at_low_selected():
    # 长期>80 且 短期≤20（今日收盘=3日最低）→ 选中
    df = _top_stall_df()
    market_cap = {"000001": 1_000_000.0}  # 100亿
    assert _run(df, market_cap=market_cap) == ["000001"]


def test_low_market_cap_excluded():
    df = _top_stall_df()
    market_cap = {"000001": 100_000.0}  # 10亿 < 50亿
    assert _run(df, market_cap=market_cap) == []


def test_no_market_cap_excluded():
    df = _top_stall_df()
    assert _run(df, market_cap=None) == []


def test_high_short_stochastic_excluded():
    # 今日收盘不贴3日最低（121 而非 120）→ 短期≈58 >20 → 排除
    df = _top_stall_df().with_columns([
        pl.when(pl.col("date") == pl.col("date").max()).then(121.0).otherwise(pl.col("close")).alias("close"),
        pl.when(pl.col("date") == pl.col("date").max()).then(120.5).otherwise(pl.col("low")).alias("low"),
    ])
    market_cap = {"000001": 1_000_000.0}
    assert _run(df, market_cap=market_cap) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_selectors/test_single_needle_down_20.py -q -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

`trendradar/domain/strategy/selectors/single_needle_down_20.py`:

```python
"""单针下20: 3日随机指标下探 ≤20 + 21日区间高位 >80 + 流通市值 ≥50亿.

TDX 公式:
  短期 = 100*(C-LLV(L,3))/(HHV(C,3)-LLV(L,3))
  长期 = 100*(C-LLV(L,21))/(HHV(C,21)-LLV(L,21))
  流通市值 = FINANCE(40)/1e8  (亿); 条件 circ_mv >= 50亿 (=500000万元)
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class SingleNeedleDown20Selector(SelectionStrategy):
    REQUIRES_MARKET_CAP = True

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        params = self.definition.default_params
        n1 = int(params.get("n1", 3))
        n2 = int(params.get("n2", 21))
        c = market_data["close"]
        low = market_data["low"]
        short = (100 * (c - low.rolling_min(n1)) /
                 (c.rolling_max(n1) - low.rolling_min(n1)))
        long_ = (100 * (c - low.rolling_min(n2)) /
                 (c.rolling_max(n2) - low.rolling_min(n2)))
        df = market_data.with_columns([
            short.alias("short_stoch"), long_.alias("long_stoch"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        short_max = float(params.get("short_max", 20))
        long_min = float(params.get("long_min", 80))
        circ_mv_min = float(params.get("circ_mv_min_yi", 50)) * 10000.0  # 亿→万元

        selected = []
        market_cap = context.market_cap or {}
        for code, hist in warmup.grouped.items():
            latest = hist.row(-1, named=True)
            if latest["short_stoch"] is None or latest["long_stoch"] is None:
                continue
            if latest["short_stoch"] > short_max:
                continue
            if latest["long_stoch"] <= long_min:
                continue
            mv = market_cap.get(code)
            if mv is None or mv < circ_mv_min:
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

- [ ] **Step 4: Register + update contract count**

`selectors/__init__.py` — add import and registration (after `big_bullish_volume` block):

```python
from trendradar.domain.strategy.selectors.single_needle_down_20 import SingleNeedleDown20Selector
...
    register(StrategyDefinition(
        strategy_id="single_needle_down_20", name="单针下20",
        description="3日随机指标下探≤20 + 21日区间高位>80 + 流通市值≥50亿",
        selector_class=SingleNeedleDown20Selector,
        default_params={"n1": 3, "n2": 21, "short_max": 20,
                        "long_min": 80, "circ_mv_min_yi": 50},
    ))
```

`tests/interfaces/test_api_contract.py:234`: `assert len(payload["strategies"]) == 9` → `== 10`.

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/domain/test_selectors/test_single_needle_down_20.py tests/domain/test_selectors/test_deterministic_baseline.py -q -p no:cacheprovider`
Expected: PASS. Then full suite: `python3 -m pytest -q -p no:cacheprovider` → 271 passed.

- [ ] **Step 6: Commit**

```bash
git add trendradar/domain/strategy/selectors/ tests/interfaces/test_api_contract.py
git commit -m "feat: 单针下20 selection strategy (needle-down stochastic + market cap)"
```

### Task 4: selection_service wiring (TDD)

**Files:**
- Modify: `trendradar/app/services/selection_service.py`
- Test: `tests/app/test_selection_market_cap.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""selection_service wires REQUIRES_MARKET_CAP strategies with per-day market cap."""
from datetime import date
from unittest.mock import MagicMock, patch

from trendradar.app.services import selection_service as svc


def test_build_market_cap_map_success_and_degrade():
    pro = MagicMock()
    dates = [date(2026, 8, 20), date(2026, 8, 21)]
    mc = {date(2026, 8, 21): {"000001": 1e6}}
    with patch.object(svc, "daily_basic_circ_mv", side_effect=[mc[date(2026, 8, 21)], Exception("boom")]):
        result = svc._build_market_cap_map(pro, dates)
    assert result[date(2026, 8, 21)] == {"000001": 1e6}
    assert result[date(2026, 8, 20)] is None  # 失败降级为 None，不抛错
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/app/test_selection_market_cap.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: module has no attribute '_build_market_cap_map'`)

- [ ] **Step 3: Implement**

In `selection_service.py`:

```python
def _needs_market_cap(resolved) -> bool:
    return any(getattr(d.selector_class, "REQUIRES_MARKET_CAP", False) for d in resolved)


def _build_market_cap_map(pro, trading_dates) -> dict:
    """Per-date {code: circ_mv} map; fetch failures degrade to None (never raise)."""
    from trendradar.infrastructure.tushare.market_cap import daily_basic_circ_mv
    result: dict = {}
    for d in trading_dates:
        try:
            result[d] = daily_basic_circ_mv(pro, d)
        except Exception as e:
            result[d] = None
    return result
```

In `_run_selection`, after `resolved` (before the per-day loop):

```python
    need_mcap = _needs_market_cap(resolved)
    mc_map: dict = {}
    if need_mcap:
        from trendradar.infrastructure.tushare.client import get_pro
        ctx.log("策略需要流通市值，按交易日拉取 daily_basic")
        mc_map = _build_market_cap_map(get_pro(), trading_dates)
```

In the per-day loop context construction:

```python
        context = SelectionContext(
            trade_date=trade_date,
            market_data=market_data,
            candidate_codes=candidate_codes,
            market_cap=mc_map.get(trade_date),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/app/test_selection_market_cap.py -q -p no:cacheprovider`
Expected: PASS. Full suite: `python3 -m pytest -q -p no:cacheprovider` → 272 passed.

- [ ] **Step 5: Commit**

```bash
git add trendradar/app/services/selection_service.py tests/app/test_selection_market_cap.py
git commit -m "feat: wire on-demand market cap into selection context (REQUIRES_MARKET_CAP)"
```

### Task 5: Final verification & deployment

- [ ] Full suite: `python3 -m pytest -q -p no:cacheprovider` → 272 passed
- [ ] Sanity: `python3 -c "import sys; sys.path.insert(0,'.'); from trendradar.domain.strategy.selectors import register_all; from trendradar.domain.strategy.registry import list_all; register_all(); print(len(list_all()))"` → 10
- [ ] Rebuild + redeploy: `./scripts/restart.sh`
- [ ] Live check: `curl -s http://localhost:8818/api/strategies | python3 -c "import json,sys; d=json.load(sys.stdin); print([s['name'] for s in d['strategies']])"` → contains 单针下20
- [ ] Optionally run a real single-day selection with 单针下20 via API and confirm it completes (market cap fetched, no failure)

---

## Sequencing

Tasks 1→2→3→4 sequential (each builds on the previous; Task 4 imports Task 2's helper and Task 3's capability). Task 5 last. Every task ends with a green suite; commit after each.

## Spec coverage map

| Spec section | Task |
|---|---|
| §3.1 SelectionContext.market_cap + REQUIRES_MARKET_CAP | 1, 3 |
| §3.2 daily_basic_circ_mv + cache | 2 |
| §3.3 selection_service assembly + degrade | 4 |
| §3.4 selector formula/params/register | 3 |
| §5 test plan (protocol/helper/selector/contract) | 1, 2, 3, 4 |
