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
