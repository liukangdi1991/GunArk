from __future__ import annotations

from typing import Dict, List

import numpy as np
import polars as pl

from selection.formulas.indicators import compute_zx_lines


class VolumeSpikeBalanceSelector:
    """倍量多空平衡策略"""

    def __init__(
        self,
        *,
        volume_spike_lookback: int = 30,
        volume_spike_multiple: float = 2.0,
        zx_stick_window: int = 10,
        zx_stick_limit_threshold: float = 0.05,
        min_spike_elapsed_days: int = 20,
        min_history: int | None = None,
    ) -> None:
        if volume_spike_lookback < 1:
            raise ValueError("volume_spike_lookback 应 >= 1")
        if volume_spike_multiple <= 0:
            raise ValueError("volume_spike_multiple 应 > 0")
        if zx_stick_window < 1:
            raise ValueError("zx_stick_window 应 >= 1")
        if zx_stick_limit_threshold <= 0:
            raise ValueError("zx_stick_limit_threshold 应 > 0")
        if min_spike_elapsed_days < 0:
            raise ValueError("min_spike_elapsed_days 应 >= 0")

        self.volume_spike_lookback = int(volume_spike_lookback)
        self.volume_spike_multiple = float(volume_spike_multiple)
        self.zx_stick_window = int(zx_stick_window)
        self.zx_stick_limit_threshold = float(zx_stick_limit_threshold)
        self.min_spike_elapsed_days = int(min_spike_elapsed_days)
        self.zx_long_window = 114
        default_min_history = self.required_history_length()
        self.min_history = int(min_history) if min_history is not None else default_min_history

    def required_history_length(self) -> int:
        zx_eval_window = max(self.volume_spike_lookback, self.zx_stick_window)
        return max(
            self.zx_long_window + zx_eval_window - 1,
            self.volume_spike_lookback + 1,
            self.zx_stick_window,
        )

    @staticmethod
    def _finite(value: float) -> bool:
        return np.isfinite(value)

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist is None or hist.is_empty():
            return False

        hist = hist.sort("date")
        if len(hist) < self.min_history:
            return False

        short_line, long_line = compute_zx_lines(hist)
        close = hist["close"].cast(pl.Float64).to_numpy()
        volume = hist["volume"].cast(pl.Float64).to_numpy()
        short = short_line.cast(pl.Float64).to_numpy()
        long = long_line.cast(pl.Float64).to_numpy()
        n = len(hist)

        close_today = float(close[-1])
        long_today = float(long[-1])
        if not (self._finite(close_today) and self._finite(long_today)):
            return False
        if not (close_today < long_today):
            return False
        if not self._passes_recent_sticky(short, long, n):
            return False

        spike_start = max(1, n - self.volume_spike_lookback)
        for idx in range(spike_start, n):
            c_prev = float(close[idx - 1])
            c_now = float(close[idx])
            v_prev = float(volume[idx - 1])
            v_now = float(volume[idx])
            if not all(self._finite(value) for value in [c_prev, c_now, v_prev, v_now]):
                continue
            if v_prev <= 0:
                continue
            if not (c_now > c_prev and v_now > v_prev * self.volume_spike_multiple):
                continue
            if not self._passes_window_from_spike(idx, volume, n):
                continue
            return True
        return False

    def _passes_recent_sticky(self, short: np.ndarray, long: np.ndarray, n: int) -> bool:
        recent_start = n - self.zx_stick_window
        if recent_start < 0:
            return False

        for idx in range(recent_start, n):
            long_value = float(long[idx])
            short_value = float(short[idx])
            if not (self._finite(long_value) and self._finite(short_value)):
                return False
            if abs(long_value) <= 1e-12:
                return False
            ratio = abs(long_value - short_value) / abs(long_value)
            if ratio >= self.zx_stick_limit_threshold:
                return False
        return True

    def _passes_window_from_spike(
        self,
        spike_idx: int,
        volume: np.ndarray,
        n: int,
    ) -> bool:
        elapsed_days = n - spike_idx
        if elapsed_days <= self.min_spike_elapsed_days:
            return False

        volume_window = volume[spike_idx:n]
        volume_window = volume_window[np.isfinite(volume_window)]
        if len(volume_window) < elapsed_days:
            return False
        volume_today = float(volume_window[-1])
        return volume_today <= float(np.min(volume_window))

    def select(
        self,
        date,
        data: Dict[str, pl.DataFrame],
        skip_day_check: bool = False,
        skip_zx_check: bool = False,
    ) -> List[str]:
        picks: List[str] = []
        need_len = max(
            self.min_history,
            self.required_history_length(),
        )
        for code, df in data.items():
            if df is None or df.is_empty():
                continue
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < need_len:
                continue
            if self._passes_filters(hist):
                picks.append(code)
        return picks
