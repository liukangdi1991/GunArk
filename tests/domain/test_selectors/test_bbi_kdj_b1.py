from datetime import date
from trendradar.domain.strategy.registry import get
from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import make_bbi_kdj_b1_defn, make_empty_df, make_ohlcv_df


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
    defn = make_bbi_kdj_b1_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "bbi_kdj_b1"
    assert result.elapsed_seconds >= 0


def test_non_empty_returns_list():
    defn = make_bbi_kdj_b1_defn()
    df = make_ohlcv_df("000001", 150, trend=0.002)
    result = _run(defn, df)
    assert isinstance(result.selected_codes, list)
    assert result.strategy_id == "bbi_kdj_b1"
    assert result.elapsed_seconds >= 0


def test_warmup_has_no_unused_columns():
    """warmup 不应携带 select_day 从未使用的列（ma_60 / zx 线），避免死计算。"""
    defn = make_bbi_kdj_b1_defn()
    df = make_ohlcv_df("000001", 150, trend=0.002)
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    cols = set(warmup.grouped["000001"].columns)
    assert not ({"ma_60", "short_term_trend_line", "long_term_bull_bear_line"} & cols)
