"""多空趋势平衡（duo_kong_trend_balance）选择行为验证。

策略语义：近 10 个交易日（含当日）**每天都**满足 |短期线−多空线|/多空线 < 0.3%，
且当日成交量排近 10 日倒数第 3 以内（地量），流通市值 ≥ 50 亿。
"""

from datetime import date

import polars as pl

from trendradar.domain.strategy.protocol import SelectionContext
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
      "adj_factor": [1.0] * days,
      "is_suspended": [False] * days,
    })


def test_converged_low_volume_selected():
    """恒定价（线完全重合）+ 今日量最低 → 入选。"""
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    # 最后一天的量改为极低（地量确认）
    last_date = df["date"][-1]
    df = df.with_columns(
        pl.when(pl.col("date") == last_date).then(500.0).otherwise(pl.col("volume")).alias("volume")
    )
    result = _run(defn, df, market_cap={"000001": 600_000})
    assert result.selected_codes == ["000001"]
    assert result.strategy_id == "duo_kong_trend_balance"
    assert result.elapsed_seconds >= 0


def test_one_day_divergence_not_selected():
    """近 10 日中有一天短/长线偏离超过阈值 → 全部条件失败 → 不入选。"""
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    # 最后第 3 天的收盘改为 15（EMA 快速偏离，MA 慢速滞后 → 短/长线显著分离）
    spike_date = df["date"][-3]
    df = df.with_columns(
        pl.when(pl.col("date") == spike_date).then(15.0).otherwise(pl.col("close")).alias("close")
    )
    result = _run(defn, df, market_cap={"000001": 600_000})
    assert result.selected_codes == []


def test_volume_too_high_not_selected():
    """今日放量（非地量）→ 不入选。"""
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    last_date = df["date"][-1]
    df = df.with_columns(
        pl.when(pl.col("date") == last_date).then(5_000_000.0).otherwise(pl.col("volume")).alias("volume")
    )
    result = _run(defn, df, market_cap={"000001": 600_000})
    assert result.selected_codes == []


def test_market_cap_below_min_not_selected():
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    result = _run(defn, df, market_cap={"000001": 490_000})  # 49 亿 < 50 亿
    assert result.selected_codes == []


def test_market_cap_boundary_selected():
    """恰好 50 亿（500000 万）含等号 → 入选。"""
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    result = _run(defn, df, market_cap={"000001": 500_000})  # 恰好 50 亿
    assert result.selected_codes == ["000001"]


def test_market_cap_missing_not_selected():
    defn = make_duo_kong_trend_balance_defn()
    df = _flat_ohlcv("000001", 200, price=10.0)
    result = _run(defn, df, market_cap=None)  # 市值数据缺失：无法判定 → 不入选
    assert result.selected_codes == []


def test_empty_data():
    defn = make_duo_kong_trend_balance_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "duo_kong_trend_balance"
    assert result.elapsed_seconds >= 0
