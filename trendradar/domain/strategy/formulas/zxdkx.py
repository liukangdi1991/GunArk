import logging

from trendradar.domain.market.adjust import apply_qfq, apply_qfq_grouped
import polars as pl


logger = logging.getLogger(__name__)


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
    """前复权口径：先按守卫缩放（复用 market/adjust.apply_qfq_grouped），再算双线。

    复权按 code 分域执行（复审 I1）：选股侧送入的是全市场拼接帧，整帧
    全局 shift 会跨票边界破恒等式 → 恒退化原价，同一票的线值随 universe
    大小漂移。分域后守卫/缩放都以单票为域，一段污染不毒化其他段。
    向量化实现（复审性能项）：多票帧走 apply_qfq_grouped 一次成列，要求
    入参按 (code, date) 排序（唯一选股入口已保证）；无 code 列的单票帧
    （图表路径）整帧一次 apply_qfq，行为不变。

    守卫任一命中（因子缺失/null/≤0/NaN/未重建形态恒1.0或混合/相邻比越带/
    恒等式校验失败）→ 该段退化原价；任一段退化记 WARNING（数据问题需可见），
    因子列缺失按存量口径静默走原价。
    """

    if "code" not in df.columns:
        df_qfq, degraded = apply_qfq(df)
        if degraded and "adj_factor" in df.columns:
            logger.warning("qfq 守卫未过，整帧退化原价")
        return compute_zx_lines(df_qfq, m1=m1, m2=m2, m3=m3, m4=m4)

    df_qfq, degraded = apply_qfq_grouped(df)
    if degraded:
        logger.warning("qfq 守卫未过：部分段退化原价（按段隔离，详见 adjust 守卫语义）")
    return compute_zx_lines(df_qfq, m1=m1, m2=m2, m3=m3, m4=m4)




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
