import polars as pl
from trendradar.domain.strategy.formulas.ma import compute_ma, compute_dif


def test_compute_ma():
    df = pl.DataFrame({"close": [10, 20, 30, 40, 50]})
    result = compute_ma(df, window=3)
    assert len(result) == 5
    assert result.name == "ma_3"
    # rolling_mean: first 2 null, then (10+20+30)/3=20, (20+30+40)/3=30, (30+40+50)/3=40
    assert result[2] == 20.0
    assert result[3] == 30.0
    assert result[4] == 40.0


def test_compute_ma_custom_column():
    df = pl.DataFrame({"high": [10, 20, 30, 40, 50]})
    result = compute_ma(df, window=2, column="high")
    assert len(result) == 5
    assert result.name == "ma_2"
    assert result[1] == 15.0


def test_compute_dif():
    df = pl.DataFrame({"close": [10.0] * 100})
    # Constant close => EWM fast == EWM slow (both converge to 10), DIF = 0
    result = compute_dif(df, fast=12, slow=26)
    assert len(result) == 100
    assert result.name == "dif"
    valid = result.drop_nulls()
    for v in valid.tail(10):
        assert abs(v) < 0.01


def test_compute_dif_uptrend():
    # In uptrend, fast EMA should lead slow EMA, DIF > 0 eventually
    df = pl.DataFrame({"close": list(range(1, 101))})
    result = compute_dif(df, fast=3, slow=10)
    assert result.name == "dif"
    valid = result.drop_nulls()
    assert len(valid) > 0
    # Last values should be positive (fast EMA above slow EMA in uptrend)
    assert valid[-1] > 0
