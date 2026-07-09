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


class SuperB1SelectionRunner(DefaultSelectionRunner):
    """SuperB1 Runner：Polars 预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = []

    selector: SuperB1Selector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = self.selector.lookback_n + self.selector._extra_for_bbi
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
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & daily_price_guard(close=close, high=high, low=low, prev_close=pl.col("prev_close"))
                # SuperB1 的末日跌幅约束
                & (drop_from_previous(pl.col("prev_close"), close) >= self.selector.price_drop_pct)
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
