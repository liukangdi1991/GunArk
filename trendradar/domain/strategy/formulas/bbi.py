import polars as pl
from trendradar.domain.strategy.formulas.ma import compute_ma


def compute_bbi(df: pl.DataFrame, windows: tuple[int, ...] = (3, 6, 12, 24)) -> pl.Series:
    close = df["close"]
    mas = [compute_ma(df, w) for w in windows]
    bbi = sum(mas) / len(mas)
    return bbi.alias("bbi")


def bbi_deriv_uptrend(
    bbi: pl.Series,
    min_window: int,
    max_window: int,
    q_threshold: float = 0.0,
) -> bool:
    if len(bbi) < max_window:
        return False
    recent = bbi.tail(max_window)
    deriv = recent.diff()
    uptrend_count = (deriv > q_threshold).sum()
    return uptrend_count >= min_window
