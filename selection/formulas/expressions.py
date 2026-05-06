from __future__ import annotations

import polars as pl


def open_expr() -> pl.Expr:
    return pl.col("open").cast(pl.Float64)


def close_expr() -> pl.Expr:
    return pl.col("close").cast(pl.Float64)


def high_expr() -> pl.Expr:
    return pl.col("high").cast(pl.Float64)


def low_expr() -> pl.Expr:
    return pl.col("low").cast(pl.Float64)


def volume_expr() -> pl.Expr:
    return pl.col("volume").cast(pl.Float64)


def previous(expr: pl.Expr) -> pl.Expr:
    return expr.shift(1).over("code")


def rolling_min(expr: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return expr.rolling_min(window_size=window, min_samples=min_samples).over("code")


def rolling_max(expr: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return expr.rolling_max(window_size=window, min_samples=min_samples).over("code")


def rolling_sum(expr: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return expr.rolling_sum(window_size=window, min_samples=min_samples).over("code")


def rolling_any(flag: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return rolling_max(flag.cast(pl.Int8), window, min_samples=min_samples)


def rolling_all(flag: pl.Expr, window: int, *, min_samples: int) -> pl.Expr:
    return rolling_min(flag.cast(pl.Int8), window, min_samples=min_samples)


def moving_average(close: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return close.rolling_mean(window_size=window, min_samples=min_samples).over("code")


def previous_rolling_mean(expr: pl.Expr, window: int, *, min_samples: int = 1) -> pl.Expr:
    return expr.shift(1).rolling_mean(window_size=window, min_samples=min_samples).over("code")


def dif_line(close: pl.Expr) -> pl.Expr:
    return (
        close.ewm_mean(span=12, adjust=False).over("code")
        - close.ewm_mean(span=26, adjust=False).over("code")
    )


def bbi_line(close: pl.Expr) -> pl.Expr:
    return (
        moving_average(close, 3)
        + moving_average(close, 6)
        + moving_average(close, 12)
        + moving_average(close, 24)
    ).truediv(4.0)


def short_term_trend_line(close: pl.Expr) -> pl.Expr:
    return close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code")


def long_term_bull_bear_line(close: pl.Expr) -> pl.Expr:
    return (
        moving_average(close, 14, min_samples=14)
        + moving_average(close, 28, min_samples=28)
        + moving_average(close, 57, min_samples=57)
        + moving_average(close, 114, min_samples=114)
    ).truediv(4.0)


def daily_price_guard(
    *,
    close: pl.Expr,
    high: pl.Expr,
    low: pl.Expr,
    prev_close: pl.Expr,
    pct_abs_limit: float = 0.02,
    amplitude_limit: float = 0.07,
) -> pl.Expr:
    return (
        (prev_close > 0)
        & (low > 0)
        & ((close / prev_close - 1.0).abs() < pct_abs_limit)
        & (((high - low) / low) < amplitude_limit)
    )


def pct_change(close: pl.Expr, prev_close: pl.Expr) -> pl.Expr:
    return close / prev_close - 1.0


def relative_spread(upper: pl.Expr, lower: pl.Expr) -> pl.Expr:
    return upper / lower - 1.0


def amplitude(high: pl.Expr, low: pl.Expr) -> pl.Expr:
    return (high - low) / low


def drop_from_previous(prev_close: pl.Expr, close: pl.Expr) -> pl.Expr:
    return (prev_close - close) / prev_close


def body_high(open_col: pl.Expr, close: pl.Expr) -> pl.Expr:
    return pl.max_horizontal(open_col, close)


def body_low(open_col: pl.Expr, close: pl.Expr) -> pl.Expr:
    return pl.min_horizontal(open_col, close)


def upper_wick_ratio(high: pl.Expr, open_col: pl.Expr, close: pl.Expr) -> pl.Expr:
    high_body = body_high(open_col, close)
    return (high - high_body) / high_body


def rsv_low_bound(low: pl.Expr, window: int) -> pl.Expr:
    return rolling_min(low, window, min_samples=1)


def rsv_high_bound(high_or_close: pl.Expr, window: int) -> pl.Expr:
    return rolling_max(high_or_close, window, min_samples=1)


def ma_cross_up(close: pl.Expr, ma: pl.Expr) -> pl.Expr:
    return ((previous(close) < previous(ma)) & (close >= ma)).cast(pl.Int8)


def rsv_from_range(close: pl.Expr, low: pl.Expr, high: pl.Expr) -> pl.Expr:
    return (close - low) / (high - low + 1e-9) * 100.0


def volume_spike_flag(close: pl.Expr, volume: pl.Expr, multiple: float) -> pl.Expr:
    prev_close = previous(close)
    prev_volume = previous(volume)
    return ((close > prev_close) & (prev_volume > 0) & (volume > prev_volume * multiple)).cast(pl.Int8)


def volume_step_down_flag(volume: pl.Expr) -> pl.Expr:
    return (volume < previous(volume)).cast(pl.Int16)


def volume_step_down_count(volume: pl.Expr, window: int) -> pl.Expr:
    return rolling_sum(volume_step_down_flag(volume), window, min_samples=window)


def recent_low(expr: pl.Expr, window: int) -> pl.Expr:
    return rolling_min(expr, window, min_samples=window)


def zx_stick_ratio(long_line: pl.Expr, short_line: pl.Expr) -> pl.Expr:
    return (long_line - short_line).abs() / long_line.abs()
