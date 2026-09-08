import polars as pl


def compute_zx_lines(
    df: pl.DataFrame,
    m1: int = 14,
    m2: int = 28,
    m3: int = 57,
    m4: int = 114,
) -> tuple[pl.Series, pl.Series]:
    """多空线与短期趋势线（用户 TDX 原文公式，2026-09-08 策略口径对齐）。

    短期趋势 = EMA(EMA(C,10),10)：Y=(2X+9Y')/11 ⟺ ewm(alpha=2/11, adjust=False)
    双重平滑（TDX EMA(X,N) 递推式，自首根收敛、无 null 预热）。
    多空 = (MA(C,m1)+MA(C,m2)+MA(C,m3)+MA(C,m4))/4。
    """
    close = df["close"]
    ma1 = close.rolling_mean(m1)
    ma2 = close.rolling_mean(m2)
    ma3 = close.rolling_mean(m3)
    ma4 = close.rolling_mean(m4)
    long_line = (ma1 + ma2 + ma3 + ma4) / 4

    e1 = close.ewm_mean(alpha=2 / 11, adjust=False)
    short_line = e1.ewm_mean(alpha=2 / 11, adjust=False)

    return short_line.alias("short_term_trend_line"), long_line.alias("long_term_bull_bear_line")


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
