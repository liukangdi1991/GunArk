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
