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

from selection.selectors.bbi_kdj import BBIKDJSelector

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
