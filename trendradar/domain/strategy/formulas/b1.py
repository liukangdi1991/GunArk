"""B1战法（bbi_kdj_b1）——通达信原公式逐行实现。

对应公式：
  DUAN_QI:=EMA(EMA(C,10),10);
  DUO_KONG:=(MA(CLOSE,M1)+MA(CLOSE,M2)+MA(CLOSE,M3)+MA(CLOSE,M4))/4;  (M1=14 M2=28 M3=57 M4=114)
  REQUIRED:=DUAN_QI>DUO_KONG;
  RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;
  K:=SMA(RSV,3,1); D:=SMA(K,3,1); J:=3*K-2*D;
  ZHEN_FU:=(HIGH-LOW)/REF(CLOSE,1)*100;
  ZHANG_FU:=(CLOSE-REF(CLOSE,1))/REF(CLOSE,1)*100;
  MA60:=MA(CLOSE,60);
  DIF:=EMA(CLOSE,12)-EMA(CLOSE,26); DEA:=EMA(DIF,9);
  BLZ:=VOL>REF(VOL,1)*2; BLZ_EXIST:=COUNT(BLZ,20)>=1;
  流通市值:=FINANCE(40)/100000000;
  XG:J<13 AND ZHEN_FU<7 AND ZHANG_FU<2 AND CLOSE>MA60 AND DIF>DEA
     AND BLZ_EXIST AND REQUIRED AND 流通市值>=50;

通达信语义映射：
- EMA(X,N)：ewm_mean(alpha=2/(N+1), adjust=False)（首值=X[0]）
- SMA(X,N,M) 递归 ≡ ewm_mean(alpha=M/N)（此处 K/D 用 SMA(RSV,3,1)）
- MA/LLV/HHV/COUNT 窗口不足 N 用已有数据 → polars rolling_* 全部 min_samples=1
- REF(X,1)=shift(1)；COUNT 含当日
- 流通市值≥50 亿：由 selector 读 context.market_cap（circ_mv 万元 ≥ 500000）
所有统计按 code 分组，不跨股票。
"""
from __future__ import annotations

import polars as pl

# 公式固定常量
J_THRESHOLD = 13.0
ZHEN_FU_MAX = 7.0
ZHANG_FU_MAX = 2.0
BLZ_VOL_RATIO = 2.0
BLZ_WINDOW = 20
DEFAULT_M_WINDOWS = (14, 28, 57, 114)


def compute_b1_columns(
    df: pl.DataFrame,
    m_windows: tuple[int, int, int, int] = DEFAULT_M_WINDOWS,
) -> pl.DataFrame:
    """为每行计算 _b1_signal（B1 条件，不含市值判定）。"""
    if df.is_empty():
        return df.with_columns(pl.lit(False).alias("_b1_signal"))
    parts = [_compute_one(g, m_windows) for g in df.partition_by("code")]
    return pl.concat(parts)


def _compute_one(g: pl.DataFrame, m_windows: tuple[int, int, int, int]) -> pl.DataFrame:
    c = pl.col("close")
    h = pl.col("high")
    lo = pl.col("low")
    v = pl.col("volume")
    pc = c.shift(1)
    pv = v.shift(1)

    # DUAN_QI := EMA(EMA(C,10),10)；DUO_KONG := (MA14+MA28+MA57+MA114)/4
    duan_qi = c.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
    duo_kong = sum(c.rolling_mean(w, min_samples=1) for w in m_windows) / 4
    required = duan_qi > duo_kong

    # KDJ（SMA(RSV,3,1) ≡ ewm alpha=1/3）
    llv9 = lo.rolling_min(9, min_samples=1)
    hhv9 = h.rolling_max(9, min_samples=1)
    rsv = ((c - llv9) / (hhv9 - llv9 + 1e-10) * 100).fill_nan(50).fill_null(50)
    k = rsv.ewm_mean(alpha=1 / 3, adjust=False)
    d = k.ewm_mean(alpha=1 / 3, adjust=False)
    j = 3 * k - 2 * d
    j_ok = j < J_THRESHOLD

    # 振幅 / 涨跌幅（相对昨收，%）
    zhen_fu = (h - lo) / pc * 100
    zhang_fu = (c - pc) / pc * 100
    zhen_fu_ok = zhen_fu < ZHEN_FU_MAX
    zhang_fu_ok = zhang_fu < ZHANG_FU_MAX

    c_gt_ma60 = c > c.rolling_mean(60, min_samples=1)

    dif = c.ewm_mean(alpha=2 / 13, adjust=False) - c.ewm_mean(alpha=2 / 27, adjust=False)
    dea = dif.ewm_mean(alpha=2 / 10, adjust=False)
    dif_gt_dea = dif > dea

    blz = (v > BLZ_VOL_RATIO * pv).fill_null(False)
    blz_exist = blz.cast(pl.Int32).rolling_sum(BLZ_WINDOW, min_samples=1) >= 1

    b1 = (j_ok & zhen_fu_ok & zhang_fu_ok & c_gt_ma60
          & dif_gt_dea & blz_exist & required)
    return g.with_columns(b1.fill_null(False).alias("_b1_signal"))
