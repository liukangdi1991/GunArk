from datetime import date
from trendradar.domain.strategy.registry import get
from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import make_perfect_b1_defn, make_empty_df, make_ohlcv_df


def _make_context(df):
    codes = df["code"].unique().to_list()
    return SelectionContext(
        trade_date=date(2026, 7, 9),
        market_data=df,
        candidate_codes=codes,
    )


def _run(defn, df):
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    return sel.select_day(_make_context(df), warmup)


def test_empty_data():
    defn = make_perfect_b1_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "perfect_b1_v2"
    assert result.elapsed_seconds >= 0


def test_non_empty_returns_list():
    defn = make_perfect_b1_defn()
    df = make_ohlcv_df("000001", 150, trend=0.002)
    result = _run(defn, df)
    assert isinstance(result.selected_codes, list)
    assert result.strategy_id == "perfect_b1_v2"
    assert result.elapsed_seconds >= 0


def test_low_null_does_not_crash():
    """最后一行 low 为 None 时不得崩溃，该股应被跳过而非抛 TypeError。"""
    import polars as pl
    defn = make_perfect_b1_defn()
    # 下跌趋势保证 J 低位，使代码走到振幅守卫（否则 J 门先拦截，测不到 low 路径）
    df = make_ohlcv_df("000001", 150, trend=-0.002)
    df = df.with_columns(
        pl.when(pl.col("date") == df["date"].max()).then(None).otherwise(pl.col("low")).alias("low")
    )
    result = _run(defn, df)
    assert isinstance(result.selected_codes, list)
    assert "000001" not in result.selected_codes
