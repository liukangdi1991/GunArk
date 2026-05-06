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


class PeakKDJSelectionRunner(DefaultSelectionRunner):
    """PeakKDJ Runner：Polars 预筛 + 原逻辑精筛。"""
    _has_zx_prefilter: bool = True
    _prefetch_indicators: List[str] = ["KDJ"]

    selector: PeakKDJSelector

    def _get_need_len(self) -> int:
        return self.selector.max_window + 20

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = self.selector.max_window + 20
        close = close_expr()
        high = high_expr()
        low = low_expr()
        prev_close = previous(close)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                prev_close.alias("prev_close"),
                short_term_trend_line(close).alias("SHORT_TERM_TREND_LINE"),
                long_term_bull_bear_line(close).alias("LONG_TERM_BULL_BEAR_LINE"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                daily_price_guard(close=close, high=high, low=low, prev_close=pl.col("prev_close"))
                # 知行末日条件
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
