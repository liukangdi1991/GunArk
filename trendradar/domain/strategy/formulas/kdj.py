import polars as pl


def compute_kdj(df: pl.DataFrame, n: int = 9) -> tuple[pl.Series, pl.Series, pl.Series]:
    low = df["low"]
    high = df["high"]
    close = df["close"]

    hhv = high.rolling_max(n)
    llv = low.rolling_min(n)

    rsv = ((close - llv) / (hhv - llv + 1e-10)) * 100
    rsv = rsv.fill_nan(50).fill_null(50)

    k = rsv.ewm_mean(alpha=1/3, adjust=False)
    d = k.ewm_mean(alpha=1/3, adjust=False)
    j = 3 * k - 2 * d

    return k.alias("k"), d.alias("d"), j.alias("j")


def compute_rsv(df: pl.DataFrame, n: int = 9) -> pl.Series:
    low = df["low"]
    high = df["high"]
    close = df["close"]
    hhv = high.rolling_max(n)
    llv = low.rolling_min(n)
    rsv = ((close - llv) / (hhv - llv + 1e-10)) * 100
    return rsv.fill_nan(50).fill_null(50).alias("rsv")
