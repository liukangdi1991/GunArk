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


class BigBullishVolumeSelectionRunner(DefaultSelectionRunner):
    """BigBullishVolume Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: BigBullishVolumeSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = max(self.selector.min_history, self.selector.vol_lookback_n + 2)
        open_col = open_expr()
        close = close_expr()
        high = high_expr()
        low = low_expr()
        volume = volume_expr()
        prev_close = previous(close)
        max_oc = body_high(open_col, close)
        min_oc = body_low(open_col, close)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
                # 用滚动均量做粗筛（精筛阶段仍按原逻辑复核）
                previous_rolling_mean(
                    volume,
                    self.selector.vol_lookback_n,
                    min_samples=max(3, int(self.selector.vol_lookback_n * 0.6)),
                ).alias("avg_vol_prev"),
                short_term_trend_line(close).alias("SHORT_TERM_TREND_LINE"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("prev_close") > 0)
                & (close > 0)
                & (high >= max_oc)
                & (low <= min_oc)
                & (pct_change(close, pl.col("prev_close")) > self.selector.up_pct_threshold)
                & (upper_wick_ratio(high, open_col, close) < self.selector.upper_wick_pct_max)
                & (pl.col("avg_vol_prev") > 0)
                & (volume >= self.selector.vol_multiple * pl.col("avg_vol_prev"))
                & (pl.col("SHORT_TERM_TREND_LINE").is_not_null())
                & (close < pl.col("SHORT_TERM_TREND_LINE") * self.selector.close_lt_short_term_trend_line_mult)
                & ((close >= open_col) if self.selector.require_bullish_close else pl.lit(True))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
