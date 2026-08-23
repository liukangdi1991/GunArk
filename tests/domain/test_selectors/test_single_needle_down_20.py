"""单针下20: 3-day stochastic ≤ 20 AND 21-day stochastic > 80 AND circ_mv ≥ 50亿."""
from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.protocol import SelectionContext
from trendradar.domain.strategy.selectors.single_needle_down_20 import (
    SingleNeedleDown20Selector,
)


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
    LLV(L,21)=93.06, HHV(C,21)=121, C=120 → 长期=100*(26.94/27.94)≈96.4 >80 ✅
    """
    n = 25
    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(n)]
    closes = [float(90 + i) for i in range(21)] + [120.0, 120.8, 121.0, 120.0]
    highs = [c * 1.01 for c in closes[:21]] + [121.2, 121.5, 121.8, 120.8]
    lows = [c * 0.99 for c in closes[:21]] + [119.5, 119.9, 120.5, 120.0]
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


def test_flat_three_day_span_excluded():
    """最后 3 日一字板（HHV==LLV）→ short_stoch 为 NaN/null → 应排除。"""
    n = 25
    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(n)]
    closes = [float(8 + i) for i in range(22)] + [30.0, 30.0, 30.0]
    highs = [c * 1.01 for c in closes[:22]] + [30.0, 30.0, 30.0]
    lows = [c * 0.99 for c in closes[:22]] + [30.0, 30.0, 30.0]
    df = pl.DataFrame({"code": ["000001"] * n, "date": dates, "open": closes,
                       "close": closes, "high": highs, "low": lows, "volume": [1e6] * n})
    assert _run(df, market_cap={"000001": 1_000_000.0}) == []
