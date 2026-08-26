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
  BLZ:=VOL>REF(VOL,1)*2; BLZ_EXIST:=COUNT(BLZ,20)>=1;
  流通市值:=FINANCE(40)/100000000;
  XG:J<13 AND 振幅<7 AND 涨幅<2 AND 涨幅>-2 AND CLOSE>MA60
     AND 存在倍量柱 AND 趋势存在 AND 流通市值>=50 AND 收盘限制;
  其中 收盘限制:=CLOSE>=DUO_KONG；MA1/MA2（MA60/EMA13）为公式内定义，XG 未使用。

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
ZHANG_FU_MIN = -2.0
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


def _prev_close_expr(g: pl.DataFrame) -> pl.Expr:
    """通达信除权处理：REF(C,1) 用可比昨收（pre_close）；旧数据缺失时回退原始昨收。"""
    if "pre_close" in g.columns:
        return pl.col("pre_close")
    return pl.col("close").shift(1)


def _qfq_scale(g: pl.DataFrame) -> pl.Expr | None:
    """通达信默认前复权：指标基于复权价，scale = adj_factor/最新因子。

    除权窗口内的均线若用未复权价会把除权前高价混入（如 688553 的
    MA114 虚高 28%），导致多空线/MA60/趋势线误判。无 adj_factor 列
    或因子无效（旧数据/测试 fixture）时返回 None，退化用原价。
    """
    if "adj_factor" not in g.columns:
        return None
    latest = g["adj_factor"][-1]
    if latest is None or latest <= 0:
        return None
    return pl.col("adj_factor") / latest


def _compute_one(g: pl.DataFrame, m_windows: tuple[int, int, int, int]) -> pl.DataFrame:
    c = pl.col("close")
    h = pl.col("high")
    lo = pl.col("low")
    v = pl.col("volume")
    pc = _prev_close_expr(g)
    pv = v.shift(1)

    # 指标（MA/EMA/RSV/趋势线）用前复权价；振幅/涨幅用实际价 + 可比昨收
    # （(C-昨收可比)/昨收可比 与复权口径数学等价）
    scale = _qfq_scale(g)
    if scale is not None:
        cq = pl.col("close") * scale
        hq = pl.col("high") * scale
        lq = pl.col("low") * scale
    else:
        cq, hq, lq = c, h, lo

    # DUAN_QI := EMA(EMA(C,10),10)；DUO_KONG := (MA14+MA28+MA56+MA114)/4
    duan_qi = cq.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
    duo_kong = sum(cq.rolling_mean(w, min_samples=1) for w in m_windows) / 4
    required = duan_qi > duo_kong

    # KDJ（SMA(RSV,3,1) ≡ ewm alpha=1/3）
    llv9 = lq.rolling_min(9, min_samples=1)
    hhv9 = hq.rolling_max(9, min_samples=1)
    rsv = ((cq - llv9) / (hhv9 - llv9 + 1e-10) * 100).fill_nan(50).fill_null(50)
    k = rsv.ewm_mean(alpha=1 / 3, adjust=False)
    d = k.ewm_mean(alpha=1 / 3, adjust=False)
    j = 3 * k - 2 * d
    j_ok = j < J_THRESHOLD

    # 振幅 / 涨跌幅（相对昨收，%）
    zhen_fu = (h - lo) / pc * 100
    zhang_fu = (c - pc) / pc * 100
    zhen_fu_ok = zhen_fu < ZHEN_FU_MAX
    zhang_fu_ok = zhang_fu < ZHANG_FU_MAX
    zhang_fu_lower_ok = zhang_fu > ZHANG_FU_MIN

    c_gt_ma60 = cq > cq.rolling_mean(60, min_samples=1)

    blz = (v > BLZ_VOL_RATIO * pv).fill_null(False)
    blz_exist = blz.cast(pl.Int32).rolling_sum(BLZ_WINDOW, min_samples=1) >= 1

    # 收盘限制：CLOSE >= 长期多空线
    close_ge_duo_kong = cq >= duo_kong

    b1 = (j_ok & zhen_fu_ok & zhang_fu_ok & zhang_fu_lower_ok
          & c_gt_ma60 & blz_exist & required & close_ge_duo_kong)
    return g.with_columns(b1.fill_null(False).alias("_b1_signal"))
