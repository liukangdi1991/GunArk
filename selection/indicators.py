"""
Selector 的 Polars 版本
- 使用 Polars + numba 计算指标（更快）
- 结果与原版完全一致
"""

from typing import Dict, List, Optional, Any

from scipy.signal import find_peaks
import numpy as np
import polars as pl
from numba import njit

# numba JIT 编译 KDJ 计算
@njit
def _compute_kdj_numba(rsv):
    """KDJ 的纯数值内核，供 Polars 侧批量调用。"""
    n = len(rsv)
    K = np.zeros(n)
    D = np.zeros(n)
    K[0] = D[0] = 50.0
    for i in range(1, n):
        K[i] = 2/3 * K[i-1] + 1/3 * rsv[i]
        D[i] = 2/3 * D[i-1] + 1/3 * K[i]
    J = 3 * K - 2 * D
    return K, D, J

# 预热 numba
_compute_kdj_numba(np.array([50.0, 60.0, 70.0]))

# --------------------------- 通用指标 --------------------------- #

def compute_kdj(df: pl.DataFrame, n: int = 9) -> pl.DataFrame:
    """使用 Polars + numba 计算 KDJ，若已有 K/D/J 列则跳过"""
    if df.is_empty():
        return df.with_columns([
            pl.lit(None).alias("K"),
            pl.lit(None).alias("D"),
            pl.lit(None).alias("J"),
        ])

    if "J" in df.columns and "K" in df.columns and "D" in df.columns:
        return df

    low_n = df["low"].rolling_min(n, min_samples=1)
    high_n = df["high"].rolling_max(n, min_samples=1)
    rsv = ((df["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy()

    K, D, J = _compute_kdj_numba(rsv)

    return df.with_columns([
        pl.Series("K", K),
        pl.Series("D", D),
        pl.Series("J", J),
    ])


def compute_bbi(df: pl.DataFrame) -> pl.Series:
    """计算 BBI（若已有 BBI 列则直接复用）。"""
    if "BBI" in df.columns:
        return df["BBI"]
    return df.select(
        (
            pl.col("close").rolling_mean(window_size=3)
            + pl.col("close").rolling_mean(window_size=6)
            + pl.col("close").rolling_mean(window_size=12)
            + pl.col("close").rolling_mean(window_size=24)
        )
        .truediv(4.0)
        .alias("BBI")
    ).to_series()


def compute_rsv(df: pl.DataFrame, n: int) -> pl.Series:
    """计算 RSV"""
    col_name = f"RSV_{n}"
    if col_name in df.columns:
        return df[col_name]
    return df.select(
        (
            (pl.col("close") - pl.col("low").rolling_min(window_size=n, min_periods=1))
            / (
                pl.col("close").rolling_max(window_size=n, min_periods=1)
                - pl.col("low").rolling_min(window_size=n, min_periods=1)
                + 1e-9
            )
            * 100.0
        ).alias(col_name)
    ).to_series()


def compute_dif(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    """计算 MACD 指标中的 DIF (EMA fast - EMA slow)"""
    if "DIF" in df.columns:
        return df["DIF"]
    return df.select(
        (
            pl.col("close").ewm_mean(span=fast, adjust=False)
            - pl.col("close").ewm_mean(span=slow, adjust=False)
        ).alias("DIF")
    ).to_series()


@njit
def _quantile_linear(arr, q):
    """numba 版线性插值分位数"""
    sorted_arr = np.sort(arr)
    n = len(sorted_arr)
    idx = q * (n - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= n:
        return sorted_arr[n - 1]
    frac = idx - lower
    return sorted_arr[lower] * (1 - frac) + sorted_arr[upper] * frac

@njit
def _bbi_deriv_uptrend_numba(bbi_arr, min_window, max_window, q_threshold):
    n = len(bbi_arr)
    if n < min_window:
        return False
    
    longest = n if max_window is None else min(n, max_window)
    
    for w in range(longest, min_window - 1, -1):
        seg = bbi_arr[n-w:]
        if seg[0] == 0:
            continue
        
        norm = seg / seg[0]
        diffs = np.empty(len(norm) - 1)
        for i in range(len(norm) - 1):
            diffs[i] = norm[i+1] - norm[i]
        
        if _quantile_linear(diffs, q_threshold) >= 0:
            return True
    return False

# 预热
_bbi_deriv_uptrend_numba(np.array([1.0, 2.0, 3.0]), 2, 3, 0.0)

def bbi_deriv_uptrend(
    bbi: pl.Series,
    *,
    min_window: int,
    max_window: int | None = None,
    q_threshold: float = 0.0,
) -> bool:
    """判断 BBI 是否"整体上升" (numba 优化版)"""
    if not 0.0 <= q_threshold <= 1.0:
        raise ValueError("q_threshold 必须位于 [0, 1] 区间内")

    bbi_arr = bbi.drop_nulls().to_numpy()
    if len(bbi_arr) < min_window:
        return False

    return _bbi_deriv_uptrend_numba(bbi_arr, min_window, max_window, q_threshold)


def _find_peaks(
    df: pl.DataFrame,
    *,
    column: str = "high",
    distance: Optional[int] = None,
    prominence: Optional[float] = None,
    height: Optional[float] = None,
    width: Optional[float] = None,
    rel_height: float = 0.5,
    **kwargs: Any,
) -> pl.DataFrame:
    """查找峰值"""
    if column not in df.columns:
        raise KeyError(f"'{column}' not found in DataFrame columns: {df.columns}")

    y = df[column].to_numpy()

    indices, props = find_peaks(
        y,
        distance=distance,
        prominence=prominence,
        height=height,
        width=width,
        rel_height=rel_height,
        **kwargs,
    )

    peaks_df = df[indices].clone()
    peaks_df = peaks_df.with_columns(pl.lit(True).alias("is_peak"))

    # Flatten SciPy arrays into columns
    for key, arr in props.items():
        if isinstance(arr, (list, np.ndarray)) and len(arr) == len(indices):
            peaks_df = peaks_df.with_columns(pl.Series(f"peak_{key}", arr))

    return peaks_df


@njit
def _last_valid_ma_cross_up_numba(close_arr, ma_arr, lookback_n):
    n = len(close_arr)
    start = 1
    if lookback_n > 0:
        s = n - lookback_n
        if s > start:
            start = s
    for i in range(n - 1, start - 1, -1):
        if i - 1 < 0:
            continue
        c_prev, c_now = close_arr[i - 1], close_arr[i]
        m_prev, m_now = ma_arr[i - 1], ma_arr[i]
        if np.isfinite(c_prev) and np.isfinite(c_now) and np.isfinite(m_prev) and np.isfinite(m_now):
            if c_prev < m_prev and c_now >= m_now:
                return i
    return -1


_last_valid_ma_cross_up_numba(np.array([1.0, 2.0]), np.array([1.5, 1.5]), 0)


def last_valid_ma_cross_up(
    close: pl.Series,
    ma: pl.Series,
    lookback_n: int | None = None,
) -> Optional[int]:
    """查找"有效上穿 MA"的最后一个交易日 T"""
    close_arr = close.to_numpy()
    ma_arr = ma.to_numpy()
    lb = lookback_n if lookback_n is not None else 0
    result = _last_valid_ma_cross_up_numba(close_arr, ma_arr, lb)
    return result if result >= 0 else None


def compute_zx_lines(
    df: pl.DataFrame,
    m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114
) -> tuple[pl.Series, pl.Series]:
    """返回 (短期趋势线, 长期多空线)。

    短期趋势线：双重平滑线
    长期多空线：多周期均线组合
    """
    if "SHORT_TERM_TREND_LINE" in df.columns and "LONG_TERM_BULL_BEAR_LINE" in df.columns:
        return df["SHORT_TERM_TREND_LINE"], df["LONG_TERM_BULL_BEAR_LINE"]
    close = df["close"].cast(pl.Float64)
    short_term_trend_line = close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False)

    ma1 = close.rolling_mean(window_size=m1, min_periods=m1)
    ma2 = close.rolling_mean(window_size=m2, min_periods=m2)
    ma3 = close.rolling_mean(window_size=m3, min_periods=m3)
    ma4 = close.rolling_mean(window_size=m4, min_periods=m4)
    long_term_bull_bear_line = (ma1 + ma2 + ma3 + ma4) / 4.0
    return short_term_trend_line, long_term_bull_bear_line


def passes_day_constraints_today(df: pl.DataFrame, pct_limit: float = 0.02, amp_limit: float = 0.07) -> bool:
    """所有战法的统一当日过滤"""
    if len(df) < 2:
        return False

    close = df["close"]
    high = df["high"]
    low = df["low"]

    close_today = float(close[-1])
    close_yest = float(close[-2])
    high_today = float(high[-1])
    low_today = float(low[-1])
    
    if close_yest <= 0 or low_today <= 0:
        return False
    pct_chg = abs(close_today / close_yest - 1.0)
    amplitude = (high_today - low_today) / low_today
    return (pct_chg < pct_limit) and (amplitude < amp_limit)


def zx_condition_at_positions(
    df: pl.DataFrame,
    *,
    require_close_gt_long: bool = True,
    require_short_gt_long: bool = True,
    pos: int | None = None,
) -> bool:
    """在指定位置 pos 检查知行条件"""
    if df.is_empty():
        return False
    short_term_trend_line, long_term_bull_bear_line = compute_zx_lines(df)
    if pos is None:
        pos = len(df) - 1

    if pos < 0 or pos >= len(df):
        return False

    s = float(short_term_trend_line[pos])
    l = float(long_term_bull_bear_line[pos]) if long_term_bull_bear_line[pos] is not None else float("nan")
    c = float(df["close"][pos])

    if not np.isfinite(l) or not np.isfinite(s):
        return False

    if require_close_gt_long and not (c > l):
        return False
    if require_short_gt_long and not (s > l):
        return False
    return True

# --------------------------- Selector 类 --------------------------- #
