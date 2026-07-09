import polars as pl


def compute_ma(df: pl.DataFrame, window: int, column: str = "close") -> pl.Series:
    return df[column].rolling_mean(window).alias(f"ma_{window}")


def compute_dif(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    close = df["close"]
    ema_fast = close.ewm_mean(span=fast, adjust=False)
    ema_slow = close.ewm_mean(span=slow, adjust=False)
    return (ema_fast - ema_slow).alias("dif")
