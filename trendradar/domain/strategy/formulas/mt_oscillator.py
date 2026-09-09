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
    """砖形图 MT 柱（用户 TDX 原文 STICKLINE 着色规则，2026-09-09 修订）。

    RED = MT > REF(MT,1)；GREEN = MT < REF(MT,1)；
    ORANGE = 前根 GREEN 且当根 RED 且 红柱长度 > 前根绿柱长度
    （(mt - prev) > (prev2 - prev)；2026-09-09 修复：原式 prev - prev2 符号写反
    致 `红柱 >= 负数` 恒真、全部绿转红误标橙——服务未重启期间用户以
    000807@2026-09-03（红 +4.73 < 绿 9.49 应为红）确认此规则后一度误改，
    已回滚维持力度过滤）。
    着色优先级：橙 > 绿 > 红（TDX STICKLINE 后画覆盖前画）；MT 持平无色（null）。
    返回 {"mt", "mt_prev", "mt_color"}：mt_prev = REF(MT,1)；mt_color ∈
    red（上行）/ green（下行）/ orange（绿后红且力度更强）/ null（持平或预热）。
    """
    mt = compute_mt(high, low, close, n=n, m=m, t=t)

    frame = pl.DataFrame({"mt": mt})
    mt_col = pl.col("mt")
    prev = mt_col.shift(1)
    prev2 = mt_col.shift(2)
    red = mt_col > prev
    green = mt_col < prev
    orange = red & (prev < prev2) & ((mt_col - prev) > (prev2 - prev))
    color = (
        pl.when(orange)
        .then(pl.lit("orange"))
        .when(green)
        .then(pl.lit("green"))
        .when(red)
        .then(pl.lit("red"))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
    )
    out = frame.select(
        pl.col("mt"),
        prev.alias("mt_prev"),
        color.alias("mt_color"),
    )
    return {name: out[name] for name in out.columns}
