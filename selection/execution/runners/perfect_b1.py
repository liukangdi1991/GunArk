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


class PerfectB1SelectionRunner(DefaultSelectionRunner):
    """PerfectB1 Runner：Polars 重预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = ["KDJ", "MA60", "ZX"]

    selector: PerfectB1Selector

    def _get_need_len(self) -> int:
        return max(
            self.selector.ma_window + 20,
            self.selector.volume_spike_lookback + 30,
            self.selector.zx_long_window + 20,
            self.selector.volume_step_down_window + 20 if self.selector.volume_step_down_window > 0 else 0,
            self.selector.recent_volume_new_low_window + 20 if self.selector.recent_volume_new_low_window > 0 else 0,
        )

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self._get_need_len()
        close = close_expr()
        high = high_expr()
        low = low_expr()
        volume = volume_expr()

        prev_close = previous(close)

        short_line = short_term_trend_line(close)
        long_line = long_term_bull_bear_line(close)

        cols = [
            pl.len().over("code").alias("hist_len"),
            prev_close.alias("prev_close"),
            moving_average(close, self.selector.ma_window).alias("MA_N"),
            short_line.alias("SHORT_TERM_TREND_LINE"),
            long_line.alias("LONG_TERM_BULL_BEAR_LINE"),
            rolling_any(
                volume_spike_flag(close, volume, self.selector.volume_spike_multiple),
                self.selector.volume_spike_lookback,
            ).alias("has_spike"),
        ]

        if self.selector.volume_step_down_window > 0:
            cols.append(
                volume_step_down_count(volume, self.selector.volume_step_down_window).alias("step_down_count")
            )

        if self.selector.recent_volume_new_low_window > 0:
            cols.append(
                recent_low(volume, self.selector.recent_volume_new_low_window).alias("recent_vol_min")
            )

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns(cols)
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= max(
                    self.selector.ma_window,
                    self.selector.volume_spike_lookback + 1,
                    self.selector.zx_long_window,
                    self.selector.volume_step_down_window + 1 if self.selector.volume_step_down_window > 0 else 0,
                    self.selector.recent_volume_new_low_window if self.selector.recent_volume_new_low_window > 0 else 0,
                    3,
                ))
                & (pl.col("prev_close") > 0)
                & (low > 0)
                & (amplitude(high, low) < self.selector.amplitude_limit)
                & (pct_change(close, pl.col("prev_close")) > self.selector.pct_chg_lower)
                & (pct_change(close, pl.col("prev_close")) < self.selector.pct_chg_upper)
                & pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null()
                & pl.col("SHORT_TERM_TREND_LINE").is_not_null()
                & (close > pl.col("MA_N"))
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("has_spike") > 0)
                & (
                    (pl.col("step_down_count") >= self.selector.min_volume_step_down_days)
                    if self.selector.volume_step_down_window > 0
                    else pl.lit(True)
                )
                & (
                    (volume <= pl.col("recent_vol_min"))
                    if self.selector.recent_volume_new_low_window > 0
                    else pl.lit(True)
                )
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
