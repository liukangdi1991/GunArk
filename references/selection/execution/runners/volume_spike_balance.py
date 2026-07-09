from __future__ import annotations

from typing import List, Optional

import polars as pl

from selection.execution.default import DefaultSelectionRunner
from selection.formulas.expressions import (
    close_expr,
    long_term_bull_bear_line,
    rolling_all,
    rolling_any,
    short_term_trend_line,
    volume_expr,
    volume_spike_flag,
    zx_stick_ratio,
)
from selection.selectors import VolumeSpikeBalanceSelector


class VolumeSpikeBalanceSelectionRunner(DefaultSelectionRunner):
    """倍量多空平衡策略 Runner：Polars 预筛 + 原逻辑精筛。"""

    _prefetch_indicators: List[str] = ["ZX"]

    selector: VolumeSpikeBalanceSelector

    def _get_need_len(self) -> int:
        return max(
            self.selector.min_history,
            self.selector.required_history_length(),
        )

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self._get_need_len()
        close = close_expr()
        volume = volume_expr()
        short_line = short_term_trend_line(close)
        long_line = long_term_bull_bear_line(close)
        spike_flag = volume_spike_flag(close, volume, self.selector.volume_spike_multiple) == 1
        sticky_flag = (
            long_line.is_not_null()
            & short_line.is_not_null()
            & (long_line.abs() > 1e-12)
            & (zx_stick_ratio(long_line, short_line) < self.selector.zx_stick_limit_threshold)
        )

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
                rolling_any(
                    spike_flag,
                    self.selector.volume_spike_lookback,
                ).alias("has_volume_spike_with_long_above_short"),
                rolling_all(
                    sticky_flag,
                    self.selector.zx_stick_window,
                    min_samples=self.selector.zx_stick_window,
                ).alias("all_recent_zx_sticky"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close < pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("all_recent_zx_sticky") == 1)
                & (pl.col("has_volume_spike_with_long_above_short") > 0)
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []
