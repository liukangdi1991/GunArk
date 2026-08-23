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
