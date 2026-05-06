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
