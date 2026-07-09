from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from typing import Callable, Dict, List, Optional

import numpy as np
import polars as pl

from selection.formulas.indicators import _compute_kdj_numba, bbi_deriv_uptrend
from selection.selectors import BBIKDJSelector
from selection.execution.base import StrategySelectionRunner
from selection.formulas.expressions import (
    bbi_line,
    close_expr,
    daily_price_guard,
    dif_line,
    high_expr,
    long_term_bull_bear_line,
    low_expr,
    ma_cross_up,
    moving_average,
    previous,
    relative_spread,
    rolling_any,
    rolling_max,
    rolling_min,
    rsv_from_range,
    short_term_trend_line,
)


class BBIKDJSelectionRunner(StrategySelectionRunner):
    """BBIKDJ 专用 Runner：大表预筛 + 候选精筛。"""

    selector: BBIKDJSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self.selector.max_window + 20
        close = close_expr()
        high = high_expr()
        low = low_expr()
        prev_close = previous(close)
        ma60 = moving_average(close, 60)

        # 预筛阶段只保留“可向量化且代价低”的条件
        filtered_codes = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                prev_close.alias("prev_close"),
                rolling_max(close, self.selector.max_window).alias("close_roll_max"),
                rolling_min(close, self.selector.max_window).alias("close_roll_min"),
                ma60.alias("MA60"),
                dif_line(close).alias("DIF"),
                short_term_trend_line(close).alias("SHORT_TERM_TREND_LINE"),
                long_term_bull_bear_line(close).alias("LONG_TERM_BULL_BEAR_LINE"),
                ma_cross_up(close, ma60).alias("cross_up"),
            ])
            .with_columns([
                rolling_any(pl.col("cross_up"), self.selector.max_window).alias("has_cross_up"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                daily_price_guard(close=close, high=high, low=low, prev_close=pl.col("prev_close"))
                & (pl.col("close_roll_min") > 0)
                & (relative_spread(pl.col("close_roll_max"), pl.col("close_roll_min")) <= self.selector.price_range_pct)
                & (close >= pl.col("MA60"))
                & (pl.col("has_cross_up") > 0)
                & (pl.col("DIF") > 0)
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
            )
            .select("code")
            .collect()
        )
        return filtered_codes["code"].to_list() if not filtered_codes.is_empty() else []

    def run_final_filter(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        candidates: Optional[List[str]],
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        if not candidates:
            return []

        need_len = self.selector.max_window + 20
        close = close_expr()

        candidates_hist = (
            data_table.lazy()
            .filter((pl.col("date") <= date_obj) & (pl.col("code").is_in(candidates)))
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                bbi_line(close).alias("BBI"),
                rolling_min(pl.col("low"), 9).alias("_low_n"),
                rolling_max(pl.col("high"), 9).alias("_high_n"),
            ])
            .with_columns([
                rsv_from_range(close, pl.col("_low_n"), pl.col("_high_n")).alias("_rsv"),
            ])
            .collect()
        )

        rsv_by_code = candidates_hist.partition_by("code", as_dict=True)
        kdj_cache = {}
        for key, df in rsv_by_code.items():
            code = key[0]
            rsv = df["_rsv"].to_numpy()
            K, D, J = _compute_kdj_numba(rsv)
            kdj_cache[code] = (K, D, J)

        candidates_hist = candidates_hist.drop(["_low_n", "_high_n", "_rsv"])

        hist_by_code = {}
        for key, df in candidates_hist.partition_by("code", as_dict=True).items():
            hist_by_code[key[0]] = df.drop("code")

        def check_bbikdj(item):
            code, hist = item
            if hist.is_empty():
                return None
            if not bbi_deriv_uptrend(
                hist["BBI"],
                min_window=self.selector.bbi_min_window,
                max_window=self.selector.max_window,
                q_threshold=self.selector.bbi_q_threshold,
            ):
                return None
            K, D, J = kdj_cache[code]
            j_today = float(J[-1])
            j_arr = J[self.selector.max_window * -1 or len(J):]
            j_arr = j_arr[np.isfinite(j_arr)]
            if len(j_arr) == 0:
                return None
            j_quantile = float(np.percentile(j_arr, self.selector.j_q_threshold * 100, interpolation="linear"))
            if not (j_today < self.selector.j_threshold or j_today <= j_quantile):
                return None
            return code

        if len(hist_by_code) > 50:
            with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
                results = pool.map(check_bbikdj, hist_by_code.items())
            return [r for r in results if r is not None]

        picks: List[str] = []
        for code in candidates:
            hist = hist_by_code.get(code)
            if hist is None or hist.is_empty():
                continue
            r = check_bbikdj((code, hist))
            if r is not None:
                picks.append(r)
        return picks
