import polars as pl
from trendradar.domain.strategy.formulas.kdj import compute_kdj, compute_rsv


def test_compute_kdj_basic():
    df = pl.DataFrame({
        "high": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        "low": [9, 10, 9, 11, 12, 13, 14, 15, 16, 17],
        "close": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    })
    k, d, j = compute_kdj(df, n=3)
    assert len(k) == 10
    assert len(d) == 10
    assert len(j) == 10
    assert k.name == "k"
    assert d.name == "d"
    assert j.name == "j"


def test_compute_kdj_range():
    df = pl.DataFrame({
        "high": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        "low": [9, 10, 9, 11, 12, 13, 14, 15, 16, 17],
        "close": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    })
    k, d, j = compute_kdj(df, n=5)
    # K, D, J should be within 0-100 range (fill_nan=50 handles initial nulls)
    valid_k = k.drop_nulls()
    valid_d = d.drop_nulls()
    valid_j = j.drop_nulls()
    if len(valid_k) > 0:
        assert valid_k.min() >= 0
        assert valid_k.max() <= 100


def test_compute_rsv():
    df = pl.DataFrame({
        "high": [11, 12, 13, 14, 15],
        "low": [9, 10, 9, 11, 12],
        "close": [10, 11, 12, 13, 14],
    })
    rsv = compute_rsv(df, n=3)
    assert len(rsv) == 5
    assert rsv.name == "rsv"
