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
class BBIKDJSelector:
    """
    自适应 *BBI(导数)* + *KDJ* 选股器
    """

    def __init__(
        self,
        j_threshold: float = -5,
        bbi_min_window: int = 90,
        max_window: int = 90,
        price_range_pct: float = 100.0,
        bbi_q_threshold: float = 0.05,
        j_q_threshold: float = 0.10,
    ) -> None:
        self.j_threshold = j_threshold
        self.bbi_min_window = bbi_min_window
        self.max_window = max_window
        self.price_range_pct = price_range_pct
        self.bbi_q_threshold = bbi_q_threshold
        self.j_q_threshold = j_q_threshold

    def _passes_filters(self, hist: pl.DataFrame, skip_day_check: bool = False, skip_zx_check: bool = False) -> bool:
        bbi = compute_bbi(hist)
        
        if not skip_day_check and not passes_day_constraints_today(hist):
            return False

        win = hist.tail(self.max_window)
        high = win["close"].max()
        low = win["close"].min()
        if low <= 0 or (high / low - 1) > self.price_range_pct:           
            return False

        if not bbi_deriv_uptrend(
            bbi,
            min_window=self.bbi_min_window,
            max_window=self.max_window,
            q_threshold=self.bbi_q_threshold,
        ):            
            return False

        j_series = compute_kdj(hist)["J"]
        j_today = float(j_series[-1])

        j_window = j_series.tail(self.max_window).drop_nulls()
        if len(j_window) == 0:
            return False
        j_quantile = float(j_window.quantile(self.j_q_threshold, interpolation="linear"))

        if not (j_today < self.j_threshold or j_today <= j_quantile):
            return False
        
        if "MA60" in hist.columns:
            ma60 = hist["MA60"]
        else:
            ma60 = hist["close"].rolling_mean(window_size=60, min_periods=1)

        if hist["close"][-1] < ma60[-1]:
            return False

        t_pos = last_valid_ma_cross_up(hist["close"], ma60, lookback_n=self.max_window)
        if t_pos is None:
            return False        
        
        dif = compute_dif(hist)
        if dif[-1] <= 0:
            return False
       
        if not skip_zx_check and not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self, date, data: Dict[str, pl.DataFrame], skip_day_check: bool = False, skip_zx_check: bool = False
    ) -> List[str]:
        picks: List[str] = []
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date)
            if hist.is_empty():
                continue
            hist = hist.tail(self.max_window + 20)
            if self._passes_filters(hist, skip_day_check=skip_day_check, skip_zx_check=skip_zx_check):
                picks.append(code)
        return picks
    
    
class SuperB1Selector:
    """SuperB1 选股器"""

    def __init__(
        self,
        *,
        lookback_n: int = 60,
        close_vol_pct: float = 0.05,
        price_drop_pct: float = 0.03,
        j_threshold: float = -5,
        j_q_threshold: float = 0.10,
        B1_params: Optional[Dict[str, Any]] = None        
    ) -> None:        
        if lookback_n < 2:
            raise ValueError("lookback_n 应 ≥ 2")
        if not (0 < close_vol_pct < 1):
            raise ValueError("close_vol_pct 应位于 (0, 1) 区间")
        if not (0 < price_drop_pct < 1):
            raise ValueError("price_drop_pct 应位于 (0, 1) 区间")
        if not (0 <= j_q_threshold <= 1):
            raise ValueError("j_q_threshold 应位于 [0, 1] 区间")
        if B1_params is None:
            raise ValueError("bbi_params没有给出")

        self.lookback_n = lookback_n
        self.close_vol_pct = close_vol_pct
        self.price_drop_pct = price_drop_pct
        self.j_threshold = j_threshold
        self.j_q_threshold = j_q_threshold

        self.bbi_selector = BBIKDJSelector(**(B1_params or {}))

        self._extra_for_bbi = self.bbi_selector.max_window + 20

    def _passes_filters(self, hist: pl.DataFrame, skip_day_check: bool = False, skip_zx_check: bool = False) -> bool:
        if len(hist) < 2:
            return False

        if not skip_day_check and not passes_day_constraints_today(hist):
            return False

        if len(hist) < self.lookback_n + self._extra_for_bbi:
            return False

        tm_found = False
        for i in range(len(hist) - 2, max(0, len(hist) - self.lookback_n - 1), -1):
            sub_hist = hist.head(i + 1)
            if self.bbi_selector._passes_filters(sub_hist):
                stable_seg = hist[i:len(hist) - 1]["close"]
                if len(stable_seg) >= 3:
                    high = stable_seg.max()
                    low = stable_seg.min()
                    if low > 0 and (high / low - 1) <= self.close_vol_pct:
                        tm_found = True
                        tm_pos = i
                        break
        
        if not tm_found:
            return False

        if not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=tm_pos):
            return False

        close_today = float(hist["close"][-1])
        close_prev = float(hist["close"][-2])
        if close_prev <= 0 or (close_prev - close_today) / close_prev < self.price_drop_pct:
            return False

        kdj = compute_kdj(hist)
        j_today = float(kdj["J"][-1])
        j_window = kdj["J"].tail(self.lookback_n).drop_nulls()
        j_q_val = float(j_window.quantile(self.j_q_threshold, interpolation="linear")) if len(j_window) > 0 else float("nan")
        if not (j_today < self.j_threshold or j_today <= j_q_val):
            return False

        if not skip_zx_check and not zx_condition_at_positions(hist, require_close_gt_long=False, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame], skip_day_check: bool = False, skip_zx_check: bool = False) -> List[str]:        
        picks: List[str] = []
        min_len = self.lookback_n + self._extra_for_bbi

        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(min_len)
            if len(hist) < min_len:
                continue
            if self._passes_filters(hist, skip_day_check=skip_day_check, skip_zx_check=skip_zx_check):
                picks.append(code)

        return picks


class PeakKDJSelector:
    """Peaks + KDJ 选股器"""

    def __init__(
        self,
        j_threshold: float = -5,
        max_window: int = 90,
        fluc_threshold: float = 0.03,
        gap_threshold: float = 0.02,
        j_q_threshold: float = 0.10,
    ) -> None:
        self.j_threshold = j_threshold
        self.max_window = max_window
        self.fluc_threshold = fluc_threshold
        self.gap_threshold = gap_threshold
        self.j_q_threshold = j_q_threshold

    def _passes_filters(self, hist: pl.DataFrame, skip_day_check: bool = False, skip_zx_check: bool = False) -> bool:
        if hist.is_empty():
            return False
        
        if not skip_day_check and not passes_day_constraints_today(hist):
            return False

        hist = hist.sort("date")
        hist = hist.with_columns(
            pl.max_horizontal("open", "close").alias("oc_max")
        )

        peaks_df = _find_peaks(
            hist,
            column="oc_max",
            distance=6,
            prominence=0.5,
        )
        
        date_today = hist["date"][-1]
        peaks_df = peaks_df.filter(pl.col("date") < date_today)
        if len(peaks_df) < 2:               
            return False

        peak_t = peaks_df.tail(1)
        oc_t = float(peak_t["oc_max"][0])
        total_peaks = len(peaks_df)

        target_peak = None        
        for idx in range(total_peaks - 2, -1, -1):
            peak_prev = peaks_df[idx]
            oc_prev = float(peak_prev["oc_max"][0])
            if oc_t <= oc_prev:
                continue

            if total_peaks >= 3 and idx < total_peaks - 2:
                inter_oc = peaks_df[idx + 1 : total_peaks - 1]["oc_max"]
                if not (inter_oc < oc_prev).all():
                    continue

            date_prev = peak_prev["date"][0]
            mask = (hist["date"] > date_prev) & (hist["date"] < peak_t["date"][0])
            min_close = hist.filter(mask)["close"].min()
            if min_close is None:
                continue
            if oc_prev <= min_close * (1 + self.gap_threshold):
                continue

            target_peak = peak_prev
            break

        if target_peak is None:
            return False

        close_today = float(hist["close"][-1])
        fluc_pct = abs(close_today - float(target_peak["close"][0])) / float(target_peak["close"][0])
        if fluc_pct > self.fluc_threshold:
            return False

        kdj = compute_kdj(hist)
        j_today = float(kdj["J"][-1])
        j_window = kdj["J"].tail(self.max_window).drop_nulls()
        if len(j_window) == 0:
            return False
        j_quantile = float(j_window.quantile(self.j_q_threshold, interpolation="linear"))
        if not (j_today < self.j_threshold or j_today <= j_quantile):
            return False

        if not skip_zx_check and not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
        skip_day_check: bool = False,
        skip_zx_check: bool = False,
    ) -> List[str]:
        picks: List[str] = []
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date)
            if hist.is_empty():
                continue
            hist = hist.tail(self.max_window + 20)
            if self._passes_filters(hist, skip_day_check=skip_day_check, skip_zx_check=skip_zx_check):
                picks.append(code)
        return picks
    

class BBIShortLongSelector:
    """BBI 上升 + 短/长期 RSV 条件 + DIF > 0 选股器"""
    def __init__(
        self,
        n_short: int = 3,
        n_long: int = 21,
        m: int = 3,
        bbi_min_window: int = 90,
        max_window: int = 150,
        bbi_q_threshold: float = 0.05,
        upper_rsv_threshold: float = 75,
        lower_rsv_threshold: float = 25
    ) -> None:
        if m < 2:
            raise ValueError("m 必须 ≥ 2")
        self.n_short = n_short
        self.n_long = n_long
        self.m = m
        self.bbi_min_window = bbi_min_window
        self.max_window = max_window
        self.bbi_q_threshold = bbi_q_threshold
        self.upper_rsv_threshold = upper_rsv_threshold
        self.lower_rsv_threshold = lower_rsv_threshold

    def _passes_filters(self, hist: pl.DataFrame, skip_day_check: bool = False, skip_zx_check: bool = False) -> bool:
        bbi = compute_bbi(hist)
        
        if not skip_day_check and not passes_day_constraints_today(hist):
            return False      

        if not bbi_deriv_uptrend(
            bbi,
            min_window=self.bbi_min_window,
            max_window=self.max_window,
            q_threshold=self.bbi_q_threshold,
        ):
            return False

        rsv_short = compute_rsv(hist, self.n_short)
        rsv_long = compute_rsv(hist, self.n_long)

        if len(hist) < self.m:
            return False

        win_short = rsv_short.tail(self.m)
        win_long = rsv_long.tail(self.m)
        long_ok = (win_long >= self.upper_rsv_threshold).all()

        short_series = win_short

        mask_upper = short_series >= self.upper_rsv_threshold
        mask_lower = short_series < self.lower_rsv_threshold

        has_upper_then_lower = False
        if mask_upper.any():
            upper_indices = np.where(mask_upper.to_numpy())[0]
            for i in upper_indices:
                if i + 1 < len(short_series) and mask_lower[i + 1:].any():
                    has_upper_then_lower = True
                    break
        
        end_ok = short_series[-1] >= self.upper_rsv_threshold

        if not (long_ok and has_upper_then_lower and end_ok):
            return False

        dif = compute_dif(hist)
        if dif[-1] <= 0:
            return False

        if not skip_zx_check and not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
        skip_day_check: bool = False,
        skip_zx_check: bool = False,
    ) -> List[str]:
        picks: List[str] = []
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date)
            if hist.is_empty():
                continue
            need_len = (
                max(self.n_short, self.n_long)
                + self.bbi_min_window
                + self.m
            )
            hist = hist.tail(max(need_len, self.max_window))
            if self._passes_filters(hist, skip_day_check=skip_day_check, skip_zx_check=skip_zx_check):
                picks.append(code)
        return picks
    
    
class MA60CrossVolumeWaveSelector:
    """上穿60放量战法"""
    def __init__(
        self,
        *,
        lookback_n: int = 60,
        vol_multiple: float = 1.5,
        j_threshold: float = -5.0,
        j_q_threshold: float = 0.10,
        ma60_slope_days: int = 5,
        max_window: int = 120,
    ) -> None:
        if lookback_n < 2:
            raise ValueError("lookback_n 应 ≥ 2")
        if not (0.0 <= j_q_threshold <= 1.0):
            raise ValueError("j_q_threshold 应位于 [0,1]")
        if ma60_slope_days < 2:
            raise ValueError("ma60_slope_days 应 ≥ 2")
        self.lookback_n = lookback_n
        self.vol_multiple = vol_multiple
        self.j_threshold = j_threshold
        self.j_q_threshold = j_q_threshold
        self.ma60_slope_days = ma60_slope_days
        self.max_window = max_window        

    @staticmethod
    def _ma_slope_positive(series: pl.Series, days: int) -> bool:
        """对最近 days 个点做一阶线性回归，斜率 > 0 判为正"""
        seg = series.drop_nulls().tail(days)
        if len(seg) < days:
            return False
        x = np.arange(len(seg), dtype=float)
        k, _ = np.polyfit(x, seg.to_numpy().astype(float), 1)
        return bool(k > 0)

    def _passes_filters(self, hist: pl.DataFrame, skip_day_check: bool = False, skip_zx_check: bool = False) -> bool:
        if hist.is_empty():
            return False

        hist = hist.sort("date")
        min_len = max(60 + self.lookback_n + self.ma60_slope_days, self.max_window + 5)
        if len(hist) < min_len:
            return False
        
        if not skip_day_check and not passes_day_constraints_today(hist):
            return False

        kdj = compute_kdj(hist)
        j_today = float(kdj["J"][-1])
        j_window = kdj["J"].tail(self.max_window).drop_nulls()
        if len(j_window) == 0:
            return False
        j_q_val = float(j_window.quantile(self.j_q_threshold, interpolation="linear"))

        if not (j_today < self.j_threshold or j_today <= j_q_val):
            return False

        if "MA60" in hist.columns:
            ma60 = hist["MA60"]
        else:
            ma60 = hist["close"].rolling_mean(window_size=60, min_periods=1)
        if hist["close"][-1] < ma60[-1]:
            return False

        t_pos = last_valid_ma_cross_up(hist["close"], ma60, lookback_n=self.lookback_n)
        if t_pos is None:
            return False

        seg_T_to_today = hist[t_pos:]
        if seg_T_to_today.is_empty():
            return False

        tmax_idx = seg_T_to_today["high"].arg_max()
        int_pos_T = t_pos
        int_pos_Tmax = t_pos + tmax_idx

        if int_pos_Tmax < int_pos_T:
            return False

        wave = hist[int_pos_T : int_pos_Tmax + 1]
        wave_len = len(wave)
        if wave_len < 3:
            return False

        pre_start_pos = max(0, int_pos_T - min(wave_len, 10))
        pre = hist[pre_start_pos:int_pos_T]
        if len(pre) < max(5, min(10, wave_len)):
            return False

        wave_vol = wave["volume"].cast(pl.Float64).replace(0, None).drop_nulls()
        pre_vol = pre["volume"].cast(pl.Float64).replace(0, None).drop_nulls()
        wave_avg_vol = float(wave_vol.mean()) if len(wave_vol) > 0 else float("nan")
        pre_avg_vol = float(pre_vol.mean()) if len(pre_vol) > 0 else float("nan")
        
        if not (np.isfinite(wave_avg_vol) and np.isfinite(pre_avg_vol) and pre_avg_vol > 0):
            return False

        if wave_avg_vol < self.vol_multiple * pre_avg_vol:
            return False

        if not self._ma_slope_positive(ma60, self.ma60_slope_days):
            return False
        
        if not skip_zx_check and not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame], skip_day_check: bool = False, skip_zx_check: bool = False) -> List[str]:
        picks: List[str] = []
        need_len = max(60 + self.lookback_n + self.ma60_slope_days, self.max_window + 20)
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < need_len:
                continue
            if self._passes_filters(hist, skip_day_check=skip_day_check, skip_zx_check=skip_zx_check):
                picks.append(code)
        return picks


class LongTermBullBearLineBalanceSelector:
    """多空平衡选股策略"""

    def __init__(
        self,
        *,
        zx_stick_limit_threshold: float = 0.004,
        close_vs_long_term_bull_bear_line_limit_threshold: float = 0.95,
        close_vs_zxdkx_limit_threshold: Optional[float] = None,
        recent_volume_window: int = 5,
    ) -> None:
        if close_vs_zxdkx_limit_threshold is not None:
            close_vs_long_term_bull_bear_line_limit_threshold = close_vs_zxdkx_limit_threshold
        if zx_stick_limit_threshold <= 0:
            raise ValueError("zx_stick_limit_threshold 应 > 0")
        if close_vs_long_term_bull_bear_line_limit_threshold <= 0:
            raise ValueError("close_vs_long_term_bull_bear_line_limit_threshold 应 > 0")
        if recent_volume_window < 2:
            raise ValueError("recent_volume_window 应 >= 2")
        self.zx_stick_limit_threshold = float(zx_stick_limit_threshold)
        self.close_vs_long_term_bull_bear_line_limit_threshold = float(
            close_vs_long_term_bull_bear_line_limit_threshold
        )
        self.recent_volume_window = int(recent_volume_window)

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist.is_empty():
            return False

        hist = hist.sort("date")
        if len(hist) < self.recent_volume_window:
            return False

        short_term_trend_line, long_term_bull_bear_line = compute_zx_lines(hist)

        short_term_trend_line_today = (
            float(short_term_trend_line[-1]) if len(short_term_trend_line) > 0 else float("nan")
        )
        long_term_bull_bear_line_today = (
            float(long_term_bull_bear_line[-1]) if len(long_term_bull_bear_line) > 0 else float("nan")
        )
        close_today = float(hist["close"][-1])

        if not (
            np.isfinite(short_term_trend_line_today)
            and np.isfinite(long_term_bull_bear_line_today)
            and np.isfinite(close_today)
        ):
            return False

        # 条件1：知行趋势线黏合限制阈值（相对差）
        if abs(long_term_bull_bear_line_today) <= 1e-12:
            return False
        zx_stick_ratio = abs(long_term_bull_bear_line_today - short_term_trend_line_today) / abs(
            long_term_bull_bear_line_today
        )
        if zx_stick_ratio >= self.zx_stick_limit_threshold:
            return False

        # 条件2：收盘价偏离长期多空线限制阈值（不低于该线 * 阈值）
        if close_today < (
            long_term_bull_bear_line_today
            * self.close_vs_long_term_bull_bear_line_limit_threshold
        ):
            return False

        # 条件3：选股当日为最近一周（交易日窗口）成交量新低
        vol_window = hist["volume"].cast(pl.Float64).tail(self.recent_volume_window).drop_nulls()
        if len(vol_window) < self.recent_volume_window:
            return False
        volume_today = float(vol_window[-1])
        min_recent = float(vol_window.min())
        if not (np.isfinite(volume_today) and np.isfinite(min_recent)):
            return False
        if volume_today > min_recent:
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
        skip_day_check: bool = False,
        skip_zx_check: bool = False,
    ) -> List[str]:
        picks: List[str] = []
        need_len = max(self.recent_volume_window, 120)
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < self.recent_volume_window:
                continue
            if self._passes_filters(hist):
                picks.append(code)
        return picks


# backward compatibility alias
ZXDKXBalanceSelector = LongTermBullBearLineBalanceSelector


class PerfectB1Selector:
    """完美B1策略"""

    def __init__(
        self,
        *,
        j_threshold: float = 13.0,
        amplitude_limit: float = 0.07,
        pct_chg_upper: float = 0.02,
        pct_chg_lower: float = -0.02,
        ma_window: int = 60,
        volume_spike_lookback: int = 20,
        volume_spike_multiple: float = 2.0,
        volume_step_down_window: int = 0,
        min_volume_step_down_days: int = 0,
        recent_volume_new_low_window: int = 0,
    ) -> None:
        if ma_window < 2:
            raise ValueError("ma_window 应 >= 2")
        if volume_spike_lookback < 1:
            raise ValueError("volume_spike_lookback 应 >= 1")
        if volume_spike_multiple <= 0:
            raise ValueError("volume_spike_multiple 应 > 0")
        if volume_step_down_window < 0:
            raise ValueError("volume_step_down_window 应 >= 0")
        if min_volume_step_down_days < 0:
            raise ValueError("min_volume_step_down_days 应 >= 0")
        if recent_volume_new_low_window < 0:
            raise ValueError("recent_volume_new_low_window 应 >= 0")
        if volume_step_down_window == 0 and min_volume_step_down_days != 0:
            raise ValueError("未启用阶梯缩量时，min_volume_step_down_days 必须为 0")
        if volume_step_down_window > 0:
            if volume_step_down_window < 1:
                raise ValueError("volume_step_down_window 启用时应 >= 1")
            if min_volume_step_down_days < 1:
                raise ValueError("min_volume_step_down_days 启用时应 >= 1")
            if min_volume_step_down_days > volume_step_down_window:
                raise ValueError("min_volume_step_down_days 不能超过 window")
        if amplitude_limit <= 0:
            raise ValueError("amplitude_limit 应 > 0")
        if pct_chg_lower >= pct_chg_upper:
            raise ValueError("pct_chg_lower 必须小于 pct_chg_upper")

        self.j_threshold = float(j_threshold)
        self.amplitude_limit = float(amplitude_limit)
        self.pct_chg_upper = float(pct_chg_upper)
        self.pct_chg_lower = float(pct_chg_lower)
        self.ma_window = int(ma_window)
        self.volume_spike_lookback = int(volume_spike_lookback)
        self.volume_spike_multiple = float(volume_spike_multiple)
        self.volume_step_down_window = int(volume_step_down_window)
        self.min_volume_step_down_days = int(min_volume_step_down_days)
        self.recent_volume_new_low_window = int(recent_volume_new_low_window)
        # compute_zx_lines 默认最长窗口 m4=114，需要至少 114 根历史才能稳定产出长期多空线
        self.zx_long_window = 114

    @staticmethod
    def _to_float(value) -> float:
        try:
            if value is None:
                return float("nan")
            return float(value)
        except Exception:
            return float("nan")

    def _has_volume_spike_bullish(self, hist: pl.DataFrame) -> bool:
        if len(hist) < 2:
            return False
        win = hist.tail(self.volume_spike_lookback + 1)
        close = win["close"].cast(pl.Float64).to_numpy()
        volume = win["volume"].cast(pl.Float64).to_numpy()
        n = len(win)
        for i in range(1, n):
            c_prev, c_now = close[i - 1], close[i]
            v_prev, v_now = volume[i - 1], volume[i]
            if not (np.isfinite(c_prev) and np.isfinite(c_now) and np.isfinite(v_prev) and np.isfinite(v_now)):
                continue
            if v_prev <= 0:
                continue
            if (c_now > c_prev) and (v_now > v_prev * self.volume_spike_multiple):
                return True
        return False

    def _has_volume_step_down(self, hist: pl.DataFrame) -> bool:
        if self.volume_step_down_window <= 0:
            return True
        if len(hist) < self.volume_step_down_window + 1:
            return False

        # 口径：最近 N 个交易日分别与其前一交易日比较，共 N 次比较
        win = hist.tail(self.volume_step_down_window + 1)
        volume = win["volume"].cast(pl.Float64).to_numpy()

        down_days = 0
        for i in range(1, len(volume)):
            v_prev, v_now = volume[i - 1], volume[i]
            if not (np.isfinite(v_prev) and np.isfinite(v_now)):
                continue
            if v_now < v_prev:
                down_days += 1

        return down_days >= self.min_volume_step_down_days

    def _is_recent_volume_new_low(self, hist: pl.DataFrame) -> bool:
        if self.recent_volume_new_low_window <= 0:
            return True
        if len(hist) < self.recent_volume_new_low_window:
            return False

        vol_window = (
            hist["volume"]
            .cast(pl.Float64)
            .tail(self.recent_volume_new_low_window)
            .drop_nulls()
        )
        if len(vol_window) < self.recent_volume_new_low_window:
            return False
        volume_today = float(vol_window[-1])
        min_recent = float(vol_window.min())
        if not (np.isfinite(volume_today) and np.isfinite(min_recent)):
            return False
        return volume_today <= min_recent

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        min_len = max(
            self.ma_window,
            self.volume_spike_lookback + 1,
            self.zx_long_window,
            self.volume_step_down_window + 1 if self.volume_step_down_window > 0 else 0,
            self.recent_volume_new_low_window if self.recent_volume_new_low_window > 0 else 0,
            3,
        )
        if hist.is_empty() or len(hist) < min_len:
            return False

        hist = hist.sort("date")

        # KDJ J < 阈值
        kdj = compute_kdj(hist)
        j_today = self._to_float(kdj["J"][-1])
        if not np.isfinite(j_today) or j_today >= self.j_threshold:
            return False

        # 当日振幅 < 阈值
        high_today = self._to_float(hist["high"][-1])
        low_today = self._to_float(hist["low"][-1])
        if not (np.isfinite(high_today) and np.isfinite(low_today)) or low_today <= 0:
            return False
        amplitude = (high_today - low_today) / low_today
        if amplitude >= self.amplitude_limit:
            return False

        # 当日涨幅在 (lower, upper) 之间
        close_today = self._to_float(hist["close"][-1])
        close_prev = self._to_float(hist["close"][-2])
        if not (np.isfinite(close_today) and np.isfinite(close_prev)) or close_prev <= 0:
            return False
        pct_chg = close_today / close_prev - 1.0
        if not (self.pct_chg_lower < pct_chg < self.pct_chg_upper):
            return False

        # 收盘价 > MA60 且 > 长期多空线
        if "MA60" in hist.columns:
            ma60_today = self._to_float(hist["MA60"][-1])
        else:
            ma60_today = self._to_float(hist["close"].rolling_mean(window_size=self.ma_window, min_periods=1)[-1])

        short_term_trend_line, long_term_bull_bear_line = compute_zx_lines(hist)
        short_term_trend_line_today = (
            self._to_float(short_term_trend_line[-1]) if len(short_term_trend_line) > 0 else float("nan")
        )
        long_term_bull_bear_line_today = (
            self._to_float(long_term_bull_bear_line[-1]) if len(long_term_bull_bear_line) > 0 else float("nan")
        )
        if not (
            np.isfinite(ma60_today)
            and np.isfinite(short_term_trend_line_today)
            and np.isfinite(long_term_bull_bear_line_today)
        ):
            return False
        if not (close_today > ma60_today and close_today > long_term_bull_bear_line_today):
            return False

        # 短期趋势线 > 长期多空线
        if not (short_term_trend_line_today > long_term_bull_bear_line_today):
            return False

        # 20日内存在倍量上涨柱
        if not self._has_volume_spike_bullish(hist):
            return False

        # 最近N个交易日内，至少K天较前一日缩量（阶梯量下跌）
        if not self._has_volume_step_down(hist):
            return False

        # 选股当日成交量为最近N天新低（含当日）
        if not self._is_recent_volume_new_low(hist):
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
        skip_day_check: bool = False,
        skip_zx_check: bool = False,
    ) -> List[str]:
        picks: List[str] = []
        need_len = max(
            self.ma_window + 20,
            self.volume_spike_lookback + 30,
            self.zx_long_window + 20,
            self.volume_step_down_window + 20 if self.volume_step_down_window > 0 else 0,
            self.recent_volume_new_low_window + 20 if self.recent_volume_new_low_window > 0 else 0,
        )
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < max(
                self.ma_window,
                self.volume_spike_lookback + 1,
                self.zx_long_window,
                self.volume_step_down_window + 1 if self.volume_step_down_window > 0 else 0,
                self.recent_volume_new_low_window if self.recent_volume_new_low_window > 0 else 0,
                3,
            ):
                continue
            if self._passes_filters(hist):
                picks.append(code)
        return picks


class BigBullishVolumeSelector:    
    """暴力K战法"""

    def __init__(
        self,
        *,
        up_pct_threshold: float = 0.04,
        upper_wick_pct_max: float = 0.5,
        vol_lookback_n: int = 20,
        vol_multiple: float = 1.5,
        min_history: int | None = None,
        require_bullish_close: bool = True,
        ignore_zero_volume: bool = True,
        close_lt_short_term_trend_line_mult: float = 1.0
    ) -> None:
        if up_pct_threshold <= 0:
            raise ValueError("up_pct_threshold 应 > 0")
        if upper_wick_pct_max < 0:
            raise ValueError("upper_wick_pct_max 应 >= 0")
        if vol_lookback_n < 1:
            raise ValueError("vol_lookback_n 应 >= 1")
        if vol_multiple <= 0:
            raise ValueError("vol_multiple 应 > 0")
        if close_lt_short_term_trend_line_mult <= 0:
            raise ValueError("close_lt_short_term_trend_line_mult 应 > 0")

        self.up_pct_threshold = float(up_pct_threshold)
        self.upper_wick_pct_max = float(upper_wick_pct_max)
        self.vol_lookback_n = int(vol_lookback_n)
        self.vol_multiple = float(vol_multiple)
        self.require_bullish_close = bool(require_bullish_close)
        self.ignore_zero_volume = bool(ignore_zero_volume)
        self.close_lt_short_term_trend_line_mult = float(close_lt_short_term_trend_line_mult)
        self.eps = float(1e-12)        
        self.min_history = int(min_history) if min_history is not None else (self.vol_lookback_n + 2)
        

    @staticmethod
    def _to_float(x) -> float:
        try:
            return float(x)
        except Exception:
            return float("nan")

    def _upper_wick_pct(self, o: float, h: float, c: float) -> float:
        return (h - max(o, c)) / max(o, c)

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist is None or hist.is_empty():
            return False

        hist = hist.sort("date")

        if len(hist) < self.min_history:
            return False
        if len(hist) < (self.vol_lookback_n + 2):
            return False

        today = hist.tail(1)
        prev = hist.tail(2).head(1)

        oT = self._to_float(today["open"][0])
        hT = self._to_float(today["high"][0])
        lT = self._to_float(today["low"][0])
        cT = self._to_float(today["close"][0])
        vT = self._to_float(today["volume"][0])

        cP = self._to_float(prev["close"][0])

        if not (np.isfinite(oT) and np.isfinite(hT) and np.isfinite(lT) and np.isfinite(cT) and np.isfinite(vT) and np.isfinite(cP)):
            return False
        if cP <= 0 or cT <= 0:
            return False
        if hT < max(oT, cT) or lT > min(oT, cT):
            return False

        if self.require_bullish_close and not (cT >= oT):
            return False

        pct_chg = cT / cP - 1.0
        if pct_chg <= self.up_pct_threshold:
            return False

        wick_pct = self._upper_wick_pct(oT, hT, cT)
        if not np.isfinite(wick_pct):
            return False
        if wick_pct >= self.upper_wick_pct_max:
            return False

        vol_hist = hist[-(self.vol_lookback_n + 1):-1]["volume"].cast(pl.Float64)
        if self.ignore_zero_volume:
            vol_hist = vol_hist.replace(0, None).drop_nulls()

        if len(vol_hist) < max(3, int(self.vol_lookback_n * 0.6)):
            return False

        avg_vol = float(vol_hist.mean())
        if not (np.isfinite(avg_vol) and avg_vol > 0):
            return False

        if vT < self.vol_multiple * avg_vol:
            return False
        
        try:
            short_term_trend_line, _ = compute_zx_lines(hist)
            short_term_trend_line_today = float(short_term_trend_line[-1])
        except Exception:
            short_term_trend_line_today = float("nan")

        if not np.isfinite(short_term_trend_line_today):
            return False
        else:
            if not (cT < short_term_trend_line_today * self.close_lt_short_term_trend_line_mult):
                return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame], skip_day_check: bool = False, skip_zx_check: bool = False) -> List[str]:
        picks: List[str] = []
        need_len = max(self.min_history, self.vol_lookback_n + 2)

        for code, df in data.items():
            if df is None or df.is_empty():
                continue
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < need_len:
                continue
            if self._passes_filters(hist):
                picks.append(code)

        return picks
