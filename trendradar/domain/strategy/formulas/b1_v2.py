"""B1战法V2（perfect_b1_v2）——通达信原公式逐行实现。

对应公式（JYZY_T/JYZY2_T 仅定义未参与 B1 输出，不实现）：
  REAL_YANG:=C>O AND NOT(C<REF(C,1));
  REAL_YIN :=C<O AND NOT(C>REF(C,1));
  RSV:=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100;
  K:=SMA(RSV,3,1); D:=SMA(K,3,1); J:=3*K-2*D; J_OK:=J<=13;
  VOL_YANG1:=SUM(VOL*REAL_YANG,57); VOL_YIN1:=SUM(VOL*REAL_YIN,57);
  VOL_YANG2:=SUM(VOL*REAL_YANG,14); VOL_YIN2:=SUM(VOL*REAL_YIN,14);
  YANGYIN_OK1:=VOL_YANG1>1.25*VOL_YIN1; YANGYIN_OK2:=VOL_YANG2>2.25*VOL_YIN2;
  O85:=LLV(O,21)+0.95*(HHV(O,21)-LLV(O,21)); TOP15O:=O>=O85;
  FD15:=C<REF(C,1) AND C<=O AND VOL>=1.2*REF(VOL,1);
  CNT28:=COUNT(TOP15O AND FD15,21); GOOD28:=CNT28<=0;
  AVG40:=MA(VOL,40);
  PLRY:=VOL>1.95*REF(VOL,1) AND C>O AND VOL>AVG40;
  PLRY_CNT:=COUNT(PLRY,14)>=2 OR COUNT(PLRY,57)>=4;
  PLRY_FIRST:=PLRY AND NOT(REF(PLRY,1)); PLRY_CONT:=PLRY AND REF(PLRY,1);
  PRE_NOT_REALYIN:=NOT(REF(REAL_YIN,1));
  HALF_DOWN:=PRE_NOT_REALYIN AND C<REF(C,1) AND VOL<=0.5*REF(VOL,1);
  CNT_FIRST/CNT_CONT/CNT_HALF:=COUNT(·,57);
  THREE_SUM_OK:=(CNT_FIRST+CNT_CONT+CNT_HALF)>=4;
  MAXVOL28:=HHV(VOL,28); MAX28_BAD:=VOL=MAXVOL28 AND REAL_YIN;
  MAX28_OK:=COUNT(MAX28_BAD,28)=0;
  A1:=(PLRY_CNT AND YANGYIN_OK1 AND J_OK AND GOOD28 AND THREE_SUM_OK AND MAX28_OK)
    OR (PLRY_CNT AND YANGYIN_OK2 AND J_OK AND GOOD28 AND THREE_SUM_OK AND MAX28_OK);
  HMSHORTWL:=SMA(SMA(C,40,4),100,50);
  HMLONGYL:=0.5*(0.2*MA(C,12)+0.3*MA(C,24)+0.3*MA(C,52)+0.2*MA(C,108))
          + 0.5*(0.4*MA(C,20)+0.25*MA(C,40)+0.25*MA(C,80)+0.1*MA(C,160));
  B1:=HMSHORTWL>=HMLONGYL*0.985 AND C>=HMLONGYL*0.985 AND A1;

通达信语义映射：
- SMA(X,N,M) 递归均线 ≡ ewm_mean(alpha=M/N, adjust=False)（首值=X[0]）
- MA/LLV/HHV/SUM/COUNT 窗口不足 N 用已有数据 → polars rolling_* 全部 min_samples=1
- REF(X,1)=shift(1)；COUNT/SUM 含当日
- MVOK（流通市值≥50 亿）不在本模块——市值按日注入，由 selector 在 select_day 判定
所有统计按 code 分组，不跨股票。
"""
from __future__ import annotations

import polars as pl

# 公式固定常量（不开放参数，保持与原公式一致）
J_THRESHOLD = 13.0
YANGYIN_RATIO_57 = 1.25
YANGYIN_RATIO_14 = 2.25
PLRY_VOL_RATIO = 1.95
FD15_VOL_RATIO = 1.2
HALF_DOWN_VOL_RATIO = 0.5
TREND_RATIO = 0.985


def compute_b1_v2_columns(df: pl.DataFrame) -> pl.DataFrame:
    """为每行计算 _b1_signal（B1 条件，不含市值/当日判定）。"""
    if df.is_empty():
        return df.with_columns(pl.lit(False).alias("_b1_signal"))
    parts = [_compute_one(g) for g in df.partition_by("code")]
    return pl.concat(parts)


def _prev_close_expr(df: pl.DataFrame) -> pl.Expr:
    """通达信除权处理：REF(C,1) 用可比昨收（pre_close）；旧数据缺失时回退原始昨收。"""
    if "pre_close" in df.columns:
        return pl.col("pre_close")
    return pl.col("close").shift(1)


def _compute_one(g: pl.DataFrame) -> pl.DataFrame:
    c = pl.col("close")
    o = pl.col("open")
    h = pl.col("high")
    lo = pl.col("low")
    v = pl.col("volume")
    pc = _prev_close_expr(g)
    pv = v.shift(1)

    real_yang = (c > o) & (~(c < pc)).fill_null(True)
    real_yin = (c < o) & (~(c > pc)).fill_null(True)

    # KDJ（SMA(RSV,3,1) 递归 ≡ ewm alpha=1/3）
    llv9 = lo.rolling_min(9, min_samples=1)
    hhv9 = h.rolling_max(9, min_samples=1)
    rsv = ((c - llv9) / (hhv9 - llv9 + 1e-10) * 100).fill_nan(50).fill_null(50)
    k = rsv.ewm_mean(alpha=1 / 3, adjust=False)
    d = k.ewm_mean(alpha=1 / 3, adjust=False)
    j = 3 * k - 2 * d
    j_ok = j <= J_THRESHOLD

    vol_yang1 = (v * real_yang).rolling_sum(57, min_samples=1)
    vol_yin1 = (v * real_yin).rolling_sum(57, min_samples=1)
    vol_yang2 = (v * real_yang).rolling_sum(14, min_samples=1)
    vol_yin2 = (v * real_yin).rolling_sum(14, min_samples=1)
    yangyin_ok1 = vol_yang1 > YANGYIN_RATIO_57 * vol_yin1
    yangyin_ok2 = vol_yang2 > YANGYIN_RATIO_14 * vol_yin2

    o_llv21 = o.rolling_min(21, min_samples=1)
    o_hhv21 = o.rolling_max(21, min_samples=1)
    o85 = o_llv21 + 0.95 * (o_hhv21 - o_llv21)
    top15o = o >= o85
    fd15 = (c < pc).fill_null(False) & (c <= o) & (v >= FD15_VOL_RATIO * pv).fill_null(False)
    cnt28 = (top15o & fd15).cast(pl.Int32).rolling_sum(21, min_samples=1)
    good28 = cnt28 <= 0

    avg40 = v.rolling_mean(40, min_samples=1)
    plry = (v > PLRY_VOL_RATIO * pv).fill_null(False) & (c > o) & (v > avg40)
    plry_cnt = plry.cast(pl.Int32).rolling_sum(14, min_samples=1) >= 2
    plry_cnt = plry_cnt | (plry.cast(pl.Int32).rolling_sum(57, min_samples=1) >= 4)
    plry_first = plry & (~plry.shift(1)).fill_null(True)
    plry_cont = plry & plry.shift(1).fill_null(False)
    pre_not_realyin = (~real_yin.shift(1)).fill_null(True)
    half_down = pre_not_realyin & (c < pc).fill_null(False) & (v <= HALF_DOWN_VOL_RATIO * pv).fill_null(False)
    three_sum_ok = (
        plry_first.cast(pl.Int32).rolling_sum(57, min_samples=1)
        + plry_cont.cast(pl.Int32).rolling_sum(57, min_samples=1)
        + half_down.cast(pl.Int32).rolling_sum(57, min_samples=1)
    ) >= 4

    maxvol28 = v.rolling_max(28, min_samples=1)
    max28_bad = (v == maxvol28) & real_yin
    max28_ok = max28_bad.cast(pl.Int32).rolling_sum(28, min_samples=1) == 0

    a1 = plry_cnt & j_ok & good28 & three_sum_ok & max28_ok & (yangyin_ok1 | yangyin_ok2)

    # 双均线趋势过滤
    hmshortwl = c.ewm_mean(alpha=4 / 40, adjust=False).ewm_mean(alpha=50 / 100, adjust=False)
    hmlongyl = (
        0.5 * (0.2 * c.rolling_mean(12, min_samples=1)
               + 0.3 * c.rolling_mean(24, min_samples=1)
               + 0.3 * c.rolling_mean(52, min_samples=1)
               + 0.2 * c.rolling_mean(108, min_samples=1))
        + 0.5 * (0.4 * c.rolling_mean(20, min_samples=1)
                 + 0.25 * c.rolling_mean(40, min_samples=1)
                 + 0.25 * c.rolling_mean(80, min_samples=1)
                 + 0.1 * c.rolling_mean(160, min_samples=1))
    )
    b1 = (hmshortwl >= hmlongyl * TREND_RATIO) & (c >= hmlongyl * TREND_RATIO) & a1

    return g.with_columns([
        b1.fill_null(False).alias("_b1_signal"),
        real_yang.alias("real_yang"),
        real_yin.alias("real_yin"),
    ])
