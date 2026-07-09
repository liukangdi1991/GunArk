from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from selection.formulas.indicators import (
    _find_peaks,
    bbi_deriv_uptrend,
    compute_bbi,
    compute_dif,
    compute_kdj,
    compute_rsv,
    compute_zx_lines,
    last_valid_ma_cross_up,
    passes_day_constraints_today,
    zx_condition_at_positions,
)

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
