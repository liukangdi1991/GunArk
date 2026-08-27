"""MACD均线粘合（macd_ma_convergence）——通达信原公式逐行实现。

对应公式：
  MA34:=MA(C,34); MA55:=MA(C,55); MA144:=MA(C,144); MA233:=MA(C,233);
  SPREAD_SHORT:=(MA55-MA34)/MA34*100;
  COND1:=MA34<MA55 AND SPREAD_SHORT>2 AND SPREAD_SHORT<6;
  COND2:=MA144>MA233 AND (MA144-MA233)/MA233*100>1.5;
  COND3:=MA144>MA55 AND MA144>MA233;
  CENTER:=(MA34+MA55+MA144+MA233)/4;
  COND4:=MA34<CENTER*0.99;
  COND5:=C>MA55 AND C<MA144;
  MA_MIN:=MIN(MIN(MA34,MA55),MIN(MA144,MA233));
  COND6:=C>MA_MIN;
  DIFF:=EMA(C,12)-EMA(C,26);
  COND7:=DIFF>0;
  COND8:=FINANCE(40)/100000000>50;   （流通市值>50 亿，由 selector 判定）
  SELECT:COND1 AND ... AND COND8;

通达信语义映射：
- EMA(X,N)：ewm_mean(alpha=2/(N+1), adjust=False)
- MA 窗口不足 N 用已有数据 → rolling_mean min_samples=1
- 指标基于前复权价（×adj/最新adj，对齐 TDX 默认前复权）
所有统计按 code 分组，不跨股票。
"""
from __future__ import annotations

import polars as pl

MA_WINDOWS = (34, 55, 144, 233)
SPREAD_SHORT_MIN = 2.0
SPREAD_SHORT_MAX = 6.0
LONG_SPREAD_MIN = 1.5
CENTER_RATIO = 0.99


def compute_macd_ma_convergence_columns(df: pl.DataFrame) -> pl.DataFrame:
    """为每行计算 _b1_signal（SELECT 条件，不含市值判定）。"""
    if df.is_empty():
        return df.with_columns(pl.lit(False).alias("_b1_signal"))
    parts = [_compute_one(g) for g in df.partition_by("code")]
    return pl.concat(parts)


def _qfq_scale(g: pl.DataFrame) -> pl.Expr | None:
    """通达信默认前复权：scale = adj_factor/最新因子；无因子列时退化原价。"""
    if "adj_factor" not in g.columns:
        return None
    latest = g["adj_factor"][-1]
    if latest is None or latest <= 0:
        return None
    return pl.col("adj_factor") / latest


def _compute_one(g: pl.DataFrame) -> pl.DataFrame:
    c = pl.col("close")
    scale = _qfq_scale(g)
    if scale is not None:
        cq = c * scale
    else:
        cq = c

    ma34 = cq.rolling_mean(34, min_samples=1)
    ma55 = cq.rolling_mean(55, min_samples=1)
    ma144 = cq.rolling_mean(144, min_samples=1)
    ma233 = cq.rolling_mean(233, min_samples=1)

    spread_short = (ma55 - ma34) / ma34 * 100
    cond1 = (ma34 < ma55) & (spread_short > SPREAD_SHORT_MIN) & (spread_short < SPREAD_SHORT_MAX)

    long_spread = (ma144 - ma233) / ma233 * 100
    cond2 = (ma144 > ma233) & (long_spread > LONG_SPREAD_MIN)

    cond3 = (ma144 > ma55) & (ma144 > ma233)

    center = (ma34 + ma55 + ma144 + ma233) / 4
    cond4 = ma34 < center * CENTER_RATIO

    cond5 = (cq > ma55) & (cq < ma144)

    ma_min = pl.min_horizontal(ma34, ma55, ma144, ma233)
    cond6 = cq > ma_min

    dif = cq.ewm_mean(alpha=2 / 13, adjust=False) - cq.ewm_mean(alpha=2 / 27, adjust=False)
    cond7 = dif > 0

    b1 = cond1 & cond2 & cond3 & cond4 & cond5 & cond6 & cond7
    return g.with_columns(b1.fill_null(False).alias("_b1_signal"))
