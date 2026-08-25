import polars as pl


def compute_ma(df: pl.DataFrame, window: int, column: str = "close") -> pl.Series:
    return df[column].rolling_mean(window).alias(f"ma_{window}")


def compute_dif(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    close = df["close"]
    ema_fast = close.ewm_mean(span=fast, adjust=False)
    ema_slow = close.ewm_mean(span=slow, adjust=False)
    return (ema_fast - ema_slow).alias("dif")


def compute_dif_grouped(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    """DIF series computed per code: recursive ewm must never cross stocks."""
    if df.is_empty():
        return pl.Series("dif", [], dtype=pl.Float64)
    parts = []
    for g in df.partition_by("code"):
        parts.append(compute_dif(g, fast, slow))
    return pl.concat(parts).alias("dif")
