"""SuperB1战法（super_b1）——通达信原公式逐行实现。

对应公式（MA1/MA2 仅定义未参与 XG，不实现）：
  SHORT_TERM:=EMA(EMA(C,10),10);
  LIFE_LINE:=(MA(C,M1)+MA(C,M2)+MA(C,M3)+MA(C,M4))/4;   (M1=14 M2=28 M3=57 M4=114)
  TREND_EXISTED:=SHORT_TERM>LIFE_LINE;
  AMPLITUDE:=(HIGH-LOW)/REF(CLOSE,1)*100;
  INCREASE:=(CLOSE-REF(CLOSE,1))/REF(CLOSE,1)*100;
  MA60:=MA(CLOSE,60);
  DIF:=EMA(CLOSE,12)-EMA(CLOSE,26); DEA:=EMA(DIF,9);
  VOLUME_DOUBLES:=VOL>REF(VOL,1)*2; VOLUME_EXISTED:=COUNT(VOLUME_DOUBLES,120)>=1;
  PULLBACK:=ABS(C-LIFE_LINE)/LIFE_LINE<=0.016;
  XG:AMPLITUDE<7 AND INCREASE<2 AND CLOSE>MA60 AND DIF>DEA
     AND VOLUME_EXISTED AND TREND_EXISTED AND PULLBACK AND 流通市值>50;

通达信语义映射：
- EMA(X,N)：ewm_mean(alpha=2/(N+1), adjust=False)（首值=X[0]）
- MA/COUNT 窗口不足 N 用已有数据 → polars rolling_* 全部 min_samples=1
- REF(C,1) 用可比昨收（pre_close，除权处理）；缺失时回退原始昨收 shift(1)
- 流通市值>50 亿：由 selector 读 context.market_cap（circ_mv 万元 > 500000，严格大于）
所有统计按 code 分组，不跨股票。
"""
from __future__ import annotations

import polars as pl

# 公式固定常量
AMPLITUDE_MAX = 7.0
INCREASE_MAX = 2.0
PULLBACK_RATIO = 0.016
DOUBLE_VOL_RATIO = 2.0
DOUBLE_WINDOW = 120
DEFAULT_M_WINDOWS = (14, 28, 57, 114)


def compute_super_b1_columns(
    df: pl.DataFrame,
    m_windows: tuple[int, int, int, int] = DEFAULT_M_WINDOWS,
) -> pl.DataFrame:
    """为每行计算 _b1_signal（XG 条件，不含市值判定）。"""
    if df.is_empty():
        return df.with_columns(pl.lit(False).alias("_b1_signal"))
    parts = [_compute_one(g, m_windows) for g in df.partition_by("code")]
    return pl.concat(parts)


def _prev_close_expr(g: pl.DataFrame) -> pl.Expr:
    """通达信除权处理：REF(C,1) 用可比昨收（pre_close）；旧数据缺失时回退原始昨收。"""
    if "pre_close" in g.columns:
        return pl.col("pre_close")
    return pl.col("close").shift(1)


def _compute_one(g: pl.DataFrame, m_windows: tuple[int, int, int, int]) -> pl.DataFrame:
    c = pl.col("close")
    h = pl.col("high")
    lo = pl.col("low")
    v = pl.col("volume")
    pc = _prev_close_expr(g)
    pv = v.shift(1)

    # SHORT_TERM := EMA(EMA(C,10),10)；LIFE_LINE := (MA14+MA28+MA57+MA114)/4
    short_term = c.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
    life_line = sum(c.rolling_mean(w, min_samples=1) for w in m_windows) / 4
    trend_existed = short_term > life_line

    amplitude = (h - lo) / pc * 100
    increase = (c - pc) / pc * 100
    amp_ok = amplitude < AMPLITUDE_MAX
    inc_ok = increase < INCREASE_MAX

    c_gt_ma60 = c > c.rolling_mean(60, min_samples=1)

    dif = c.ewm_mean(alpha=2 / 13, adjust=False) - c.ewm_mean(alpha=2 / 27, adjust=False)
    dea = dif.ewm_mean(alpha=2 / 10, adjust=False)
    dif_gt_dea = dif > dea

    vol_doubles = (v > DOUBLE_VOL_RATIO * pv).fill_null(False)
    vol_existed = vol_doubles.cast(pl.Int32).rolling_sum(DOUBLE_WINDOW, min_samples=1) >= 1

    pullback = (c - life_line).abs() / life_line <= PULLBACK_RATIO

    b1 = amp_ok & inc_ok & c_gt_ma60 & dif_gt_dea & vol_existed & trend_existed & pullback
    return g.with_columns(b1.fill_null(False).alias("_b1_signal"))
