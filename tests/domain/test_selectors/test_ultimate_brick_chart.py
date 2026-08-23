"""极致砖型图: MT 绿转红 + 前3日绿柱 + 多头排列（close>=ZXK>DKK）。"""
from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.protocol import SelectionContext, WarmupResult
from trendradar.domain.strategy.selectors.ultimate_brick_chart import (
    UltimateBrickChartSelector,
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
    return mt, zxk


def _defn():
    return StrategyDefinition(
        strategy_id="ultimate_brick_chart", name="极致砖型图选股", description="",
        selector_class=UltimateBrickChartSelector, default_params={},
    )


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
    w = _defn().selector_class(_defn()).warmup(df)
    g = w.grouped["000001"]
    mt_r, zxk_r = _ref([c * 1.03 for c in closes], [c / 1.03 for c in closes], closes)
    got_mt = g["mt"].to_list()
    got_zxk = g["zxk"].to_list()
    for i in range(n):
        if mt_r[i] is None:
            assert got_mt[i] is None
        else:
            assert got_mt[i] is not None and abs(got_mt[i] - mt_r[i]) < 1e-6
        if zxk_r[i] is None:
            assert got_zxk[i] is None
        else:
            assert got_zxk[i] is not None and abs(got_zxk[i] - zxk_r[i]) < 1e-6


def _hist(mt5, zxk, dkk, close):
    n = 120
    return pl.DataFrame({
        "code": ["000001"] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "mt": [0.0] * (n - 5) + [float(v) for v in mt5],
        "zxk": [float(zxk)] * n, "dkk": [float(dkk)] * n, "close": [float(close)] * n,
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
        "volume": [1e6] * n,
    })


def _run(hist):
    w = WarmupResult(grouped={"000001": hist})
    ctx = SelectionContext(trade_date=hist["date"][-1], market_data=hist,
                           candidate_codes=["000001"])
    return UltimateBrickChartSelector(_defn()).select_day(ctx, w).selected_codes


def test_all_conditions_selected():
    # mt=[8,7,6,5,8]: C1 (8>5, 5<6, 3>=1) ✅ C2 (5<6,6<7,7<8) ✅
    # zxk=110 > dkk=90: C3 ✅; close=115 >= zxk=110: C4 ✅
    assert _run(_hist([8, 7, 6, 5, 8], zxk=110.0, dkk=90.0, close=115.0)) == ["000001"]


def test_c1_fails_when_mt_not_rising():
    assert _run(_hist([8, 7, 6, 5, 4], zxk=110.0, dkk=90.0, close=115.0)) == []


def test_c2_fails_when_green_chain_broken():
    assert _run(_hist([8, 7, 9, 5, 8], zxk=110.0, dkk=90.0, close=115.0)) == []


def test_c3_fails_when_zxk_below_dkk():
    assert _run(_hist([8, 7, 6, 5, 8], zxk=90.0, dkk=100.0, close=115.0)) == []


def test_c4_fails_when_close_below_zxk():
    assert _run(_hist([8, 7, 6, 5, 8], zxk=110.0, dkk=90.0, close=85.0)) == []


def test_nan_mt_excluded():
    assert _run(_hist([8, 7, 6, 5, float("nan")], zxk=110.0, dkk=90.0, close=115.0)) == []


def test_short_history_excluded():
    hist = _hist([8, 7, 6, 5, 8], zxk=110.0, dkk=90.0, close=115.0).head(10)
    assert _run(hist) == []
