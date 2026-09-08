from trendradar.domain.market.adjust import apply_qfq

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

    注意：本函数是纯序列计算，不做复权。选股场景请使用
    `compute_zx_lines_adjusted`（前复权口径，与图表一致）。
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


def compute_zx_lines_adjusted(
    df: pl.DataFrame,
    m1: int = 14,
    m2: int = 28,
    m3: int = 57,
    m4: int = 114,
) -> tuple[pl.Series, pl.Series]:
    """前复权口径：先按守卫整列缩放（复用 market/adjust.apply_qfq），再算双线。

    数据口径与图表一致（scale = adj_factor / 最新因子）。守卫任一命中（因子
    缺失/null/≤0/NaN/未重建形态恒1.0或混合/相邻比越带）→ 整列退化原价；
    存量无 adj_factor 列的旧数据自然走原价路径，行为与历史版本一致。
    """
    df_qfq, _ = apply_qfq(df)
    return compute_zx_lines(df_qfq, m1=m1, m2=m2, m3=m3, m4=m4)

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
