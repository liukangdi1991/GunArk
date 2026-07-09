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
        get_data_dict=lambda: {},
    )


def test_empty_data():
    defn = make_bbi_kdj_b1_defn()
    ctx = _make_context(make_empty_df())
    result = defn.selector_class(defn).select(ctx)
    assert result.selected_codes == []
    assert result.strategy_id == "bbi_kdj_b1"
    assert result.elapsed_seconds >= 0


def test_non_empty_returns_list():
    defn = make_bbi_kdj_b1_defn()
    df = make_ohlcv_df("000001", 150, trend=0.002)
    ctx = _make_context(df)
    result = defn.selector_class(defn).select(ctx)
    assert isinstance(result.selected_codes, list)
    assert result.strategy_id == "bbi_kdj_b1"
    assert result.elapsed_seconds >= 0
