"""多空趋势平衡（duo_kong_trend_balance）选择行为验证。

TDX 语义：近 10 个交易日（含当日）内，|短期线−多空线|/多空线 < 0.3% 的天数 ≥ 5，
且流通市值 ≥ 50 亿（含等号）。多空线 ≤ 0 或空值的交易日不参与计数。
"""

from datetime import date

import polars as pl

from trendradar.domain.strategy.protocol import SelectionContext
from trendradar.domain.strategy.selectors.duo_kong_trend_balance import count_balance_days
from .helpers import make_duo_kong_trend_balance_defn, make_empty_df


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


def _flat_ohlcv(code, days, price=10.0, overrides=None):
    """恒定价格的 OHLCV：短/长线全部收敛到同一值，条件每天都成立。"""
    dates = pl.date_range(
        start=date(2026, 1, 1), end=date(2026, 12, 31), interval="1d", eager=True,
    )[:days]
    frame = {
        "code": [code] * days, "date": dates,
        "open": [price] * days, "high": [price] * days,
        "low": [price] * days, "close": [price] * days,
        "volume": [1e6] * days,
    }
    for idx, values in (overrides or {}).items():
        for field, value in values.items():
            frame[field][idx] = value
    return pl.DataFrame(frame)


def test_count_balance_days():
    """计数语义：含当日；多空线非正/空值不计数；阈值严格小于。"""
    eq = count_balance_days([10.0] * 10, [10.0] * 10, 0.003)   # 完全贴合
    assert eq == 10
    one_in_ten = count_balance_days(
        [10.0, 10.5, 10.0, 10.5, 10.0, 10.5, 10.0, 10.5, 10.0, 10.5],
        [10.0] * 10, 0.003,
    )
    assert one_in_ten == 5  # 贴合的 5 天计入
    with_bad_long = count_balance_days(
        [10.0, 10.0, 10.0, None], [10.0, 0.0, -10.0, None], 0.003,
    )
    assert with_bad_long == 1  # 多空线 0/负/空 的日子不计
    strict = count_balance_days([10.04] * 3, [10.0] * 3, 0.003)
    assert strict == 0  # 偏离 0.4% > 0.3%：严格小于，不计
    within = count_balance_days([10.029] * 3, [10.0] * 3, 0.003)
    assert within == 3  # 偏离 0.29% < 0.3%：计入


def test_empty_data():
    defn = make_duo_kong_trend_balance_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "duo_kong_trend_balance"
    assert result.elapsed_seconds >= 0


def test_converged_stock_selected():
    """短/长线完全贴合（恒定价 → 线全部重合）：近 10 日天天满足 → 入选。"""
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 120, price=10.0)
    result = _run(defn, df, market_cap={"000001": 600_000})  # 60 亿 ≥ 50 亿
    assert result.selected_codes == ["000001"]


def test_divergent_tail_not_selected():
    """最近 10 日反复大幅振荡（偏离远超 0.3%）：贴合天数不足 → 不入选。"""
    defn = make_duo_kong_trend_balance_defn()
    overrides = {i: {"close": 12.0, "open": 12.0, "high": 12.0, "low": 12.0}
                 for i in range(110, 120) if i % 2 == 1}
    df = _flat_ohlcv("000001", 120, overrides=overrides)
    result = _run(defn, df, market_cap={"000001": 600_000})
    assert result.selected_codes == []


def test_market_cap_below_min_not_selected():
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 120)
    result = _run(defn, df, market_cap={"000001": 490_000})  # 49 亿 < 50 亿
    assert result.selected_codes == []


def test_market_cap_missing_not_selected():
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 120)
    result = _run(defn, df, market_cap=None)  # 市值数据缺失：无法判定 → 不入选
    assert result.selected_codes == []
