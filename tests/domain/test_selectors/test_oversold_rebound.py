"""超跌反弹（oversold_rebound）选择行为验证。

TDX 语义：在超跌抄底 C1/C2/C3 基础上，新增「近 6 日前 5 日 MT 连续下行（绿柱）、
当日 MT 回升（红柱）」条件。不含 C4（收盘 < ZXK）和 C5（MACD DIF 拐头）。
"""

from datetime import date

import polars as pl

from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import make_oversold_rebound_defn, make_empty_df


def _make_context(df, market_cap=None):
    codes = df["code"].unique().to_list()
    return SelectionContext(
        trade_date=date(2026, 7, 9),
        market_data=df,
        candidate_codes=codes,
        market_cap=market_cap,
    )


def _run(defn, df, market_cap=None):
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    return sel.select_day(_make_context(df, market_cap), warmup)


def _flat_ohlcv(code, days, price=10.0):
    """恒定价格的 OHLCV：短/长线全部收敛到同一值，近 10 天每天都满足平衡条件。"""
    dates = pl.date_range(
        start=date(2026, 1, 1), end=date(2026, 12, 31), interval="1d", eager=True,
    )[:days]
    return pl.DataFrame({
        "code": [code] * days, "date": dates,
        "open": [price] * days, "high": [price] * days,
        "low": [price] * days, "close": [price] * days,
        "volume": [1e6] * days,
    })


def test_empty_data():
    defn = make_oversold_rebound_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "oversold_rebound"
    assert result.elapsed_seconds >= 0
