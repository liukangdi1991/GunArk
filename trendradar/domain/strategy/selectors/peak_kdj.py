import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj_grouped


class PeakKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        j_series = compute_kdj_grouped(market_data)
        df = market_data.with_columns([j_series.alias("j")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        j_threshold = params.get("j_threshold", 10)
        max_window = params.get("max_window", 120)
        fluc_threshold = params.get("fluc_threshold", 0.03)
        gap_threshold = params.get("gap_threshold", 0.2)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            recent = hist.slice(-max_window, max_window)
            high_max = recent["high"].max()
            low_min = recent["low"].min()
            if high_max is None or low_min is None or high_max <= 0:
                continue
            fluc = (high_max - low_min) / high_max
            if fluc < fluc_threshold:
                continue
            close_now = latest["close"]
            gap = (high_max - close_now) / high_max
            if gap < gap_threshold:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
