import polars as pl


def compute_zx_lines(
    df: pl.DataFrame,
    m1: int = 14,
    m2: int = 28,
    m3: int = 57,
    m4: int = 114,
) -> tuple[pl.Series, pl.Series]:
    close = df["close"]
    ma1 = close.rolling_mean(m1)
    ma2 = close.rolling_mean(m2)

    ma3 = close.rolling_mean(m3)
    ma4 = close.rolling_mean(m4)
    long_line = (ma3 + ma3 + ma4 + ma4) / 4

    return ma1.alias("short_term_trend_line"), long_line.alias("long_term_bull_bear_line")


def zx_stick_ratio(short_line: pl.Series, long_line: pl.Series) -> pl.Series:
    return ((short_line - long_line).abs() / long_line).alias("zx_stick_ratio")


def zx_stick_condition(
    short_line: pl.Series,
    long_line: pl.Series,
    threshold: float = 0.04,
    window: int = 10,
) -> pl.Series:
    ratio = zx_stick_ratio(short_line, long_line)
    return (ratio.rolling_min(window) < threshold).alias("zx_stick_condition")
