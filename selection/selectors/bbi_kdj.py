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
