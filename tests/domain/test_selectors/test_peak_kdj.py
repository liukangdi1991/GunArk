from datetime import date
from trendradar.domain.strategy.registry import get
from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import make_peak_kdj_defn, make_empty_df, make_ohlcv_df


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
    defn = make_peak_kdj_defn()
    result = _run(defn, make_empty_df())
    assert result.selected_codes == []
    assert result.strategy_id == "peak_kdj"
    assert result.elapsed_seconds >= 0


def test_non_empty_returns_list():
    defn = make_peak_kdj_defn()
    df = make_ohlcv_df("000001", 150, trend=0.002)
    result = _run(defn, df)
    assert isinstance(result.selected_codes, list)
    assert result.strategy_id == "peak_kdj"
    assert result.elapsed_seconds >= 0
