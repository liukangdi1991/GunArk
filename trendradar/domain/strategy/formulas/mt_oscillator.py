"""TDX 砖型图/超跌抄底 MT 振荡器（SMA(X,N,1) = ewm(alpha=1/N)）。"""

from __future__ import annotations

import polars as pl


def compute_mt(high: pl.Series, low: pl.Series, close: pl.Series,
               n: int = 4, m: int = 6, t: int = 4) -> pl.Series:
    """TDX MT 振荡器: max(SMA链差值 - t, 0)。

    SMA(X,N,M) 为通达信中国式递归平滑，M=1 时等价 ewm(alpha=1/N, adjust=False)。
    """
    hh = high.rolling_max(n)
    ll = low.rolling_min(n)
    span = hh - ll
    var1 = (hh - close) / span * 100 - 90
    var2 = var1.ewm_mean(alpha=1.0 / n, adjust=False) + 100
    var3 = (close - ll) / span * 100
    var4 = var3.ewm_mean(alpha=1.0 / m, adjust=False)
    var5 = var4.ewm_mean(alpha=1.0 / m, adjust=False) + 100
    var6 = var5 - var2
    return (var6 - t).clip(lower_bound=0.0).alias("mt")


def compute_mt_brick(
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    n: int = 4,
    m: int = 6,
    t: int = 4,
) -> dict[str, pl.Series]:
    """砖形图 MT 柱（用户 TDX 原文 STICKLINE 着色规则，2026-09-08）。

    RED = MT > REF(MT,1)；GREEN = MT < REF(MT,1)；
    ORANGE = RED 且 前根 GREEN 且 本次上行情度 ≥ 前次下行情度（RED_H ≥ GREEN_H 前 1 根）。
    着色优先级：橙 > 绿 > 红（TDX STICKLINE 后画覆盖前画）；MT 持平无色（null）。
    返回 {"mt", "mt_red", "mt_green", "mt_orange"}（非命中处为 null）。
    """
    mt = compute_mt(high, low, close, n=n, m=m, t=t)

    frame = pl.DataFrame({"mt": mt})
    mt_col = pl.col("mt")
    prev = mt_col.shift(1)
    prev2 = mt_col.shift(2)
    orange_expr = (mt_col > prev) & (prev < prev2) & ((mt_col - prev) >= (prev - prev2))
    out = frame.select(
        pl.col("mt"),
        pl.when(orange_expr)
        .then(pl.col("mt"))
        .otherwise(None)
        .alias("mt_orange"),
        pl.when((mt_col > prev) & ~orange_expr)
        .then(pl.col("mt"))
        .otherwise(None)
        .alias("mt_red"),
        pl.when((mt_col < prev) & prev.is_not_null())
        .then(pl.col("mt"))
        .otherwise(None)
        .alias("mt_green"),
    )
    return {name: out[name] for name in out.columns}
