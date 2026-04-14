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
    """使用 Polars + numba 计算 KDJ"""
    if df.is_empty():
        return df.with_columns([
            pl.lit(None).alias("K"),
            pl.lit(None).alias("D"),
            pl.lit(None).alias("J"),
        ])

    # Polars 计算 RSV
    low_n = df["low"].rolling_min(n, min_samples=1)
    high_n = df["high"].rolling_max(n, min_samples=1)
    rsv = ((df["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy()

    # numba 计算 K, D, J
    K, D, J = _compute_kdj_numba(rsv)

    return df.with_columns([
        pl.Series("K", K),
        pl.Series("D", D),
        pl.Series("J", J),
    ])


def compute_bbi(df: pl.DataFrame) -> pl.Series:
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
    return df.select(
        (
            (pl.col("close") - pl.col("low").rolling_min(window_size=n, min_periods=1))
            / (
                pl.col("close").rolling_max(window_size=n, min_periods=1)
                - pl.col("low").rolling_min(window_size=n, min_periods=1)
                + 1e-9
            )
            * 100.0
        ).alias("RSV")
    ).to_series()


def compute_dif(df: pl.DataFrame, fast: int = 12, slow: int = 26) -> pl.Series:
    """计算 MACD 指标中的 DIF (EMA fast - EMA slow)"""
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


def last_valid_ma_cross_up(
    close: pl.Series,
    ma: pl.Series,
    lookback_n: int | None = None,
) -> Optional[int]:
    """查找"有效上穿 MA"的最后一个交易日 T"""
    n = len(close)
    start = 1
    if lookback_n is not None:
        start = max(start, n - lookback_n)

    close_arr = close.to_numpy()
    ma_arr = ma.to_numpy()

    # 自后向前找最后一次有效上穿
    for i in range(n - 1, start - 1, -1):
        if i - 1 < 0:
            continue
        c_prev, c_now = close_arr[i - 1], close_arr[i]
        m_prev, m_now = ma_arr[i - 1], ma_arr[i]
        if np.isfinite(c_prev) and np.isfinite(c_now) and np.isfinite(m_prev) and np.isfinite(m_now):
            if c_prev < m_prev and c_now >= m_now:
                return i
    return None


def compute_zx_lines(
    df: pl.DataFrame,
    m1: int = 14, m2: int = 28, m3: int = 57, m4: int = 114
) -> tuple[pl.Series, pl.Series]:
    """返回 (ZXDQ, ZXDKX)"""
    close = df["close"].cast(pl.Float64)
    zxdq = close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False)

    ma1 = close.rolling_mean(window_size=m1, min_periods=m1)
    ma2 = close.rolling_mean(window_size=m2, min_periods=m2)
    ma3 = close.rolling_mean(window_size=m3, min_periods=m3)
    ma4 = close.rolling_mean(window_size=m4, min_periods=m4)
    zxdkx = (ma1 + ma2 + ma3 + ma4) / 4.0
    return zxdq, zxdkx


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
    zxdq, zxdkx = compute_zx_lines(df)
    if pos is None:
        pos = len(df) - 1

    if pos < 0 or pos >= len(df):
        return False

    s = float(zxdq[pos])
    l = float(zxdkx[pos]) if zxdkx[pos] is not None else float("nan")
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

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        bbi = compute_bbi(hist)
        
        if not passes_day_constraints_today(hist):
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
        
        ma60 = hist["close"].rolling_mean(window_size=60, min_periods=1)

        if hist["close"][-1] < ma60[-1]:
            return False

        t_pos = last_valid_ma_cross_up(hist["close"], ma60, lookback_n=self.max_window)
        if t_pos is None:
            return False        

        dif = compute_dif(hist)
        if dif[-1] <= 0:
            return False
       
        if not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self, date, data: Dict[str, pl.DataFrame]
    ) -> List[str]:
        picks: List[str] = []
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date)
            if hist.is_empty():
                continue
            hist = hist.tail(self.max_window + 20)
            if self._passes_filters(hist):
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

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if len(hist) < 2:
            return False

        if not passes_day_constraints_today(hist):
            return False

        if len(hist) < self.lookback_n + self._extra_for_bbi:
            return False

        # 简化版：检查最近是否有满足条件的日期
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

        if not zx_condition_at_positions(hist, require_close_gt_long=False, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame]) -> List[str]:        
        picks: List[str] = []
        min_len = self.lookback_n + self._extra_for_bbi

        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(min_len)
            if len(hist) < min_len:
                continue
            if self._passes_filters(hist):
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

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist.is_empty():
            return False
        
        if not passes_day_constraints_today(hist):
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

        if not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
    ) -> List[str]:
        picks: List[str] = []
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date)
            if hist.is_empty():
                continue
            hist = hist.tail(self.max_window + 20)
            if self._passes_filters(hist):
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

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        bbi = compute_bbi(hist)
        
        if not passes_day_constraints_today(hist):
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

        if not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
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
            if self._passes_filters(hist):
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

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist.is_empty():
            return False

        hist = hist.sort("date")
        min_len = max(60 + self.lookback_n + self.ma60_slope_days, self.max_window + 5)
        if len(hist) < min_len:
            return False
        
        if not passes_day_constraints_today(hist):
            return False

        kdj = compute_kdj(hist)
        j_today = float(kdj["J"][-1])
        j_window = kdj["J"].tail(self.max_window).drop_nulls()
        if len(j_window) == 0:
            return False
        j_q_val = float(j_window.quantile(self.j_q_threshold, interpolation="linear"))

        if not (j_today < self.j_threshold or j_today <= j_q_val):
            return False

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
        
        if not zx_condition_at_positions(hist, require_close_gt_long=True, require_short_gt_long=True, pos=None):
            return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame]) -> List[str]:
        picks: List[str] = []
        need_len = max(60 + self.lookback_n + self.ma60_slope_days, self.max_window + 20)
        for code, df in data.items():
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < need_len:
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
        close_lt_zxdq_mult: float = 1.0
    ) -> None:
        if up_pct_threshold <= 0:
            raise ValueError("up_pct_threshold 应 > 0")
        if upper_wick_pct_max < 0:
            raise ValueError("upper_wick_pct_max 应 >= 0")
        if vol_lookback_n < 1:
            raise ValueError("vol_lookback_n 应 >= 1")
        if vol_multiple <= 0:
            raise ValueError("vol_multiple 应 > 0")
        if close_lt_zxdq_mult <= 0:
            raise ValueError("close_lt_zxdq_mult 应 > 0")    

        self.up_pct_threshold = float(up_pct_threshold)
        self.upper_wick_pct_max = float(upper_wick_pct_max)
        self.vol_lookback_n = int(vol_lookback_n)
        self.vol_multiple = float(vol_multiple)
        self.require_bullish_close = bool(require_bullish_close)
        self.ignore_zero_volume = bool(ignore_zero_volume)
        self.close_lt_zxdq_mult = float(close_lt_zxdq_mult)
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
            zxdq, _ = compute_zx_lines(hist)
            zxdq_T = float(zxdq[-1])
        except Exception:
            zxdq_T = float("nan")

        if not np.isfinite(zxdq_T):
            return False
        else:
            if not (cT < zxdq_T * self.close_lt_zxdq_mult):
                return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame]) -> List[str]:
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