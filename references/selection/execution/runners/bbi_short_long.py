from __future__ import annotations

from typing import List, Optional

import polars as pl

from selection.selectors import (
    BBIShortLongSelector,
    BigBullishVolumeSelector,
    MA60CrossVolumeWaveSelector,
    PeakKDJSelector,
    PerfectB1Selector,
    SuperB1Selector,
    ZXDKXBalanceSelector,
)
from selection.execution.default import DefaultSelectionRunner
from selection.formulas.expressions import (
    amplitude,
    body_high,
    body_low,
    close_expr,
    daily_price_guard,
    dif_line,
    drop_from_previous,
    high_expr,
    long_term_bull_bear_line,
    low_expr,
    ma_cross_up,
    moving_average,
    open_expr,
    pct_change,
    previous,
    previous_rolling_mean,
    recent_low,
    rolling_all,
    rolling_any,
    rsv_from_range,
    rsv_high_bound,
    rsv_low_bound,
    short_term_trend_line,
    upper_wick_ratio,
    volume_expr,
    volume_spike_flag,
    volume_step_down_count,
    zx_stick_ratio,
)


class BBIShortLongSelectionRunner(DefaultSelectionRunner):
    """BBIShortLong Runner：Polars 预筛 + 原逻辑精筛。"""
    _has_zx_prefilter: bool = True
    _prefetch_indicators: List[str] = ["BBI", "RSV", "DIF"]

    selector: BBIShortLongSelector

    def _get_need_len(self) -> int:
        sel = self.selector
        return max(
            max(sel.n_short, sel.n_long) + sel.bbi_min_window + sel.m,
            sel.max_window,
        )

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = max(
            max(self.selector.n_short, self.selector.n_long) + self.selector.bbi_min_window + self.selector.m,
            self.selector.max_window,
        )
        close = close_expr()
        high = high_expr()
        low = low_expr()
        prev_close = previous(close)
        low_short = rsv_low_bound(low, self.selector.n_short)
        high_close_short = rsv_high_bound(close, self.selector.n_short)
        low_long = rsv_low_bound(low, self.selector.n_long)
        high_close_long = rsv_high_bound(close, self.selector.n_long)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
                rsv_from_range(close, low_short, high_close_short).alias("RSV_short"),
                rsv_from_range(close, low_long, high_close_long).alias("RSV_long"),
                dif_line(close).alias("DIF"),
                short_term_trend_line(close).alias("SHORT_TERM_TREND_LINE"),
                long_term_bull_bear_line(close).alias("LONG_TERM_BULL_BEAR_LINE"),
            ])
            .with_columns([
                rolling_all(
                    pl.col("RSV_long").ge(self.selector.upper_rsv_threshold),
                    self.selector.m,
                    min_samples=self.selector.m,
                ).alias("long_ok_roll"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & daily_price_guard(close=close, high=high, low=low, prev_close=pl.col("prev_close"))
                # 可快速判断的必要条件
                & (pl.col("long_ok_roll") == 1)
                & (pl.col("RSV_short") >= self.selector.upper_rsv_threshold)
                & (pl.col("DIF") > 0)
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
