import polars as pl
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines, zx_stick_ratio, zx_stick_condition


def test_compute_zx_lines():
    df = pl.DataFrame({"close": list(range(120, 140))})
    short_line, long_line = compute_zx_lines(df)
    assert len(short_line) == 20
    assert len(long_line) == 20
    assert short_line.name == "short_term_trend_line"
    assert long_line.name == "long_term_bull_bear_line"


def test_compute_zx_lines_custom_windows():
    df = pl.DataFrame({"close": [10.0] * 200})
    short_line, long_line = compute_zx_lines(df, m1=5, m2=10, m3=20, m4=40)
    assert len(short_line) == 200
    assert len(long_line) == 200
    # With constant close, both lines should equal close (ignoring initial nulls)
    valid_short = short_line.drop_nulls()
    valid_long = long_line.drop_nulls()
    assert valid_short[0] == 10.0
    assert valid_long[0] == 10.0


def test_zx_stick_ratio():
    short = pl.Series("s", [10.0, 10.5, 11.0])
    long = pl.Series("l", [10.0, 10.0, 10.0])
    result = zx_stick_ratio(short, long)
    assert len(result) == 3
    assert result.name == "zx_stick_ratio"
    assert result[0] == 0.0
    assert result[1] == 0.05
    assert result[2] == 0.1


def test_zx_stick_condition():
    short = pl.Series("s", [10.0, 10.1, 10.0, 10.1, 10.0])
    long = pl.Series("l", [10.0, 10.0, 10.0, 10.0, 10.0])
    result = zx_stick_condition(short, long, threshold=0.04, window=3)
    assert len(result) == 5
    assert result.name == "zx_stick_condition"
