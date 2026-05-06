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
