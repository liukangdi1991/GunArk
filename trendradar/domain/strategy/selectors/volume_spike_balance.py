import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class VolumeSpikeBalanceSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        short_line, long_line = compute_zx_lines(market_data)
        df = market_data.with_columns([
            short_line.alias("short_term_trend_line"),
            long_line.alias("long_term_bull_bear_line"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params

        volume_spike_lookback = params.get("volume_spike_lookback", 30)
        volume_spike_multiple = params.get("volume_spike_multiple", 2.0)
        min_spike_elapsed_days = params.get("min_spike_elapsed_days", 20)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < volume_spike_lookback + 1:
                continue

            latest = hist.row(-1, named=True)
            long_line_val = latest["long_term_bull_bear_line"]
            if long_line_val is None or long_line_val <= 0:
                continue
            if latest["close"] is None or latest["close"] >= long_line_val:
                continue

            lookback = hist.slice(-volume_spike_lookback - 1, volume_spike_lookback + 1)
            spike_idx = None
            for i in range(1, len(lookback)):
                prev_row = lookback.row(i - 1, named=True)
                cur_row = lookback.row(i, named=True)
                if prev_row["volume"] is None or cur_row["volume"] is None:
                    continue
                if prev_row["volume"] <= 0:
                    continue
                if (cur_row["volume"] > prev_row["volume"] * volume_spike_multiple
                        and cur_row["close"] > cur_row["open"]):
                    spike_idx = i
                    break

            if spike_idx is None:
                continue

            days_since_spike = len(lookback) - 1 - spike_idx
            if days_since_spike < min_spike_elapsed_days:
                continue

            post_spike_vols = lookback["volume"].slice(spike_idx + 1, len(lookback) - spike_idx - 1)
            if len(post_spike_vols) > 0 and latest["volume"] > post_spike_vols.min():
                continue

            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
