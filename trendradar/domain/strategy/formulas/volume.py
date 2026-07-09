import polars as pl


def volume_spike_flag(volume: pl.Series, multiple: float = 2.0) -> pl.Series:
    prev_vol = volume.shift(1)
    return (volume > prev_vol * multiple).alias("volume_spike_flag")


def volume_step_down(volume: pl.Series, window: int = 5) -> pl.Series:
    decreased = volume.diff() < 0
    return decreased.rolling_sum(window).alias("volume_step_down")
