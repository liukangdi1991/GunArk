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


class ZXDKXBalanceSelectionRunner(DefaultSelectionRunner):
    """ZXDKXBalance Runner：Polars 预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = ["ZX"]

    selector: ZXDKXBalanceSelector

    def _get_need_len(self) -> int:
        return max(self.selector.recent_volume_window, 114) + 20

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self._get_need_len()
        close = close_expr()
        volume = volume_expr()
        short_line = short_term_trend_line(close)
        long_line = long_term_bull_bear_line(close)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                short_line.alias("SHORT_TERM_TREND_LINE"),
                long_line.alias("LONG_TERM_BULL_BEAR_LINE"),
                recent_low(volume, self.selector.recent_volume_window).alias("recent_vol_min"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= max(self.selector.recent_volume_window, 114))
                & pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null()
                & pl.col("SHORT_TERM_TREND_LINE").is_not_null()
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").abs() > 1e-12)
                & (
                    zx_stick_ratio(
                        pl.col("LONG_TERM_BULL_BEAR_LINE"),
                        pl.col("SHORT_TERM_TREND_LINE"),
                    )
                    < self.selector.zx_stick_limit_threshold
                )
                & (
                    close
                    >= pl.col("LONG_TERM_BULL_BEAR_LINE")
                    * self.selector.close_vs_long_term_bull_bear_line_limit_threshold
                )
                & pl.col("recent_vol_min").is_not_null()
                & (volume <= pl.col("recent_vol_min"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
