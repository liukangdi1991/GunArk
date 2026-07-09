import polars as pl
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend


def test_compute_bbi_basic():
    df = pl.DataFrame({"close": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]})
    bbi = compute_bbi(df)
    assert len(bbi) == 26
    assert bbi.name == "bbi"


def test_compute_bbi_custom_windows():
    df = pl.DataFrame({"close": [10, 11, 12, 13, 14, 15]})
    bbi = compute_bbi(df, windows=(2, 3))
    assert len(bbi) == 6
    assert bbi.name == "bbi"


def test_bbi_deriv_uptrend_true():
    # Create uptrend: values strictly increasing
    bbi = pl.Series("bbi", [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0])
    result = bbi_deriv_uptrend(bbi, min_window=5, max_window=10, q_threshold=0.0)
    assert result is True


def test_bbi_deriv_uptrend_false_flat():
    bbi = pl.Series("bbi", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    result = bbi_deriv_uptrend(bbi, min_window=5, max_window=10, q_threshold=0.0)
    assert result is False


def test_bbi_deriv_uptrend_false_downtrend():
    bbi = pl.Series("bbi", [20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0])
    result = bbi_deriv_uptrend(bbi, min_window=5, max_window=10, q_threshold=0.0)
    assert result is False


def test_bbi_deriv_uptrend_too_short():
    bbi = pl.Series("bbi", [10.0, 11.0, 12.0])
    result = bbi_deriv_uptrend(bbi, min_window=5, max_window=10)
    assert result is False
