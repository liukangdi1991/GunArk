"""compute_mt: TDX SMA chain verified against a plain Python reference loop.

参考实现语义与 polars 对齐：
- rolling_max/min 前 n-1 行为 None（min_samples=n）
- ewm_mean(ignore_nulls) 跳过 null，首个非空值起算
"""

from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.protocol import SelectionContext, WarmupResult
from trendradar.domain.strategy.selectors.brick_chart import (
    BrickChartSelector, compute_mt,
)


def _sma_ref(xs, n, m=1):
    """TDX SMA(X,N,M) = (M*X + (N-M)*Y_prev)/N; first value = X.

    Null/NaN 跳过（对应 polars ewm ignore_nulls）；首个非空值起算。
    """
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


def _mt_ref(highs, lows, closes, n=4, m=6, t=4):
    # rolling 窗口：前 n-1 行为 None（对齐 polars min_samples=n）
    hh = [None if i < n - 1 else max(highs[i - n + 1:i + 1]) for i in range(len(highs))]
    ll = [None if i < n - 1 else min(lows[i - n + 1:i + 1]) for i in range(len(lows))]
    var1 = [
        None if hh[i] is None or hh[i] == ll[i] else (hh[i] - closes[i]) / (hh[i] - ll[i]) * 100 - 90
        for i in range(len(closes))
    ]
    var2 = [v + 100 if v is not None else None for v in _sma_ref(var1, n, 1)]
    var3 = [
        None if hh[i] is None or hh[i] == ll[i] else (closes[i] - ll[i]) / (hh[i] - ll[i]) * 100
        for i in range(len(closes))
    ]
    var4 = _sma_ref(var3, m, 1)
    var5 = [v + 100 if v is not None else None for v in _sma_ref(var4, m, 1)]
    var6 = [var5[i] - var2[i] if var5[i] is not None and var2[i] is not None else None
            for i in range(len(closes))]
    return [None if v6 is None else max(v6 - t, 0.0) for v6 in var6]


def test_compute_mt_matches_reference():
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
        if e is None:
            assert g is None
        else:
            assert g is not None and abs(g - e) < 1e-6


def _defn():
    return StrategyDefinition(
        strategy_id="brick_chart", name="砖型图", description="",
        selector_class=BrickChartSelector, default_params={},
    )


def _hist(mt_vals, close=120.0, dkk=100.0):
    n = len(mt_vals)
    return pl.DataFrame({
        "code": ["000001"] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "mt": [float(v) for v in mt_vals],
        "close": [close] * n,
        "dkk": [dkk] * n,
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
        "volume": [1e6] * n,
    })


def _run(hist):
    warmup = WarmupResult(grouped={"000001": hist})
    ctx = SelectionContext(trade_date=hist["date"][-1], market_data=hist,
                           candidate_codes=["000001"])
    return BrickChartSelector(_defn()).select_day(ctx, warmup).selected_codes


def test_selected_when_red_after_3_green_with_enough_height():
    # 最近 5 个 MT: [8, 7, 6, 5, 8] → red=8>5, green 连 3 日, red_h=3>=green_h1=1 → 选中
    assert _run(_hist([8, 7, 6, 5, 8])) == ["000001"]


def test_excluded_when_mt_not_rising():
    assert _run(_hist([8, 7, 6, 5, 4])) == []


def test_excluded_when_green_chain_broken():
    assert _run(_hist([8, 7, 9, 5, 8])) == []


def test_excluded_when_rise_smaller_than_prior_fall():
    assert _run(_hist([8, 7, 6, 5, 5.5])) == []


def test_excluded_when_close_below_dkk():
    assert _run(_hist([8, 7, 6, 5, 8], close=100.0, dkk=120.0)) == []


def test_short_history_excluded():
    assert _run(_hist([5, 6, 5, 8])) == []
