import polars as pl


def volume_spike_flag(close: pl.Series, volume: pl.Series, multiple: float = 2.0) -> pl.Series:
    """当日成交量 > 前一日 * multiple 且 收盘价 > 开盘价。"""
    prev_vol = volume.shift(1)
    return (volume > prev_vol * multiple).alias("volume_spike_flag")


def volume_step_down(volume: pl.Series, window: int = 5) -> pl.Series:
    decreased = volume.diff() < 0
    return decreased.rolling_sum(window).alias("volume_step_down")


def recent_low(column: pl.Series, window: int) -> pl.Series:
    """检查 column 在过去 window 天内是否达到新低。"""
    return (column == column.rolling_min(window)).alias("recent_low")
