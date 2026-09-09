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
    hh = [max(highs[max(0, i - n + 1):i + 1]) for i in range(len(highs))]
    ll = [min(lows[max(0, i - n + 1):i + 1]) for i in range(len(lows))]
    var1 = [-90.0 if hh[i] == ll[i] else (hh[i] - closes[i]) / (hh[i] - ll[i]) * 100 - 90
            for i in range(len(closes))]
    var2 = [v + 100 for v in _sma_ref(var1, n, 1)]
    var3 = [0.0 if hh[i] == ll[i] else (closes[i] - ll[i]) / (hh[i] - ll[i]) * 100
            for i in range(len(closes))]
    var4 = _sma_ref(var3, m, 1)
    var5 = [v + 100 if v is not None else None for v in _sma_ref(var4, m, 1)]
    var6 = [var5[i] - var2[i] if var5[i] is not None and var2[i] is not None else None
            for i in range(len(closes))]
    mt = [None if v6 is None else max(v6 - t, 0.0) for v6 in var6]
    zxk = _ewma_ref(_ewma_ref(closes, 2 / 11), 2 / 11)
    diff = [a - b if a is not None and b is not None else None
            for a, b in zip(_ewma_ref(closes, 2 / 13), _ewma_ref(closes, 2 / 27))]
    dd2 = [None if i < 2 else closes[i] - 2 * closes[i - 1] + closes[i - 2]
           for i in range(len(closes))]
    return mt, zxk, diff, dd2


def _defn():
    return StrategyDefinition(
        strategy_id="oversold_bottom_fishing", name="超跌抄底", description="",
        selector_class=OversoldBottomFishingSelector, default_params={},
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
    mt_r, zxk_r, diff_r, dd2_r = _ref(
        [c * 1.03 for c in closes], [c / 1.03 for c in closes], closes)
    for col, exp in [("mt", mt_r), ("zxk", zxk_r), ("diff", diff_r), ("dd2", dd2_r)]:
        got = g[col].to_list()
        assert len(got) == len(exp)
        for i, (a, b) in enumerate(zip(got, exp)):
            if b is None:
                assert a is None, f"{col}[{i}]: {a} vs None"
            else:
                assert a is not None and abs(a - b) < 1e-6, f"{col}[{i}]: {a} vs {b}"


def _hist(mt5, dd2s, zxk, dkk, close, diffs):
    """构造满足/违反条件的 hist（长度 120 ≥ 115）。"""
    n = 120
    return pl.DataFrame({
        "code": ["000001"] * n,
        "date": [date(2026, 7, 1) + timedelta(days=i) for i in range(n)],
        "mt": [0.0] * (n - len(mt5)) + [float(v) for v in mt5],
        "dd2": [0.0] * (n - len(dd2s)) + [float(v) for v in dd2s],
        "diff": [0.0] * (n - len(diffs)) + [float(v) for v in diffs],
        "zxk": [float(zxk)] * n, "dkk": [float(dkk)] * n, "close": [float(close)] * n,
        "open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
        "volume": [1e6] * n,
    })


def _run(hist):
    w = WarmupResult(grouped={"000001": hist})
    ctx = SelectionContext(trade_date=hist["date"][-1], market_data=hist,
                           candidate_codes=["000001"])
    return OversoldBottomFishingSelector(_defn()).select_day(ctx, w).selected_codes


def test_all_conditions_selected():
    # mt=[8,7,6,5,8]: C1 ✅; dd2 今日 2.0 为近5日最高>0: C2 ✅
    # zxk=90 < dkk=100: C3 ✅; close=80 < zxk=90: C4 ✅
    # diff=[-2,-1.6,-1.2,-0.8,-0.4,-0.1]: 近5全负且逐日收窄(仍负), 今日回升: C5 ✅
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == ["000001"]


def test_c1_fails_when_mt_not_rising():
    hist = _hist(mt5=[8, 7, 6, 5, 4], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == []


def test_c2_fails_when_dd2_not_new_high():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[3.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == []   # dd2[-5]=3.0 > 今日 2.0


def test_c3_fails_when_zxk_above_dkk():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=110.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == []


def test_c4_fails_when_close_above_zxk():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=95.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == []


def test_c5_fails_when_diff_not_negative_streak():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-2.0, -1.6, -1.2, -0.8, 1.0, 0.1])  # 近5有正值
    assert _run(hist) == []


def test_nan_mt_excluded():
    # mt 含 NaN（0-span 一字板场景）→ 排除
    hist = _hist(mt5=[8, 7, 6, 5, float("nan")], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  )
    assert _run(hist) == []


def test_short_history_excluded():
    hist = _hist(mt5=[8, 7, 6, 5, 8], dd2s=[-1.0, -0.5, 0.2, 1.5, 2.0],
                 zxk=90.0, dkk=100.0, close=80.0,
                 diffs=[-0.2, -0.5, -0.9, -1.4, -2.0, -1.5]  ).head(10)
    assert _run(hist) == []
