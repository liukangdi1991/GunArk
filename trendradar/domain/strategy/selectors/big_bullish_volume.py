import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class BigBullishVolumeSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        grouped = {g["code"][0]: g for g in market_data.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        up_pct_threshold = params.get("up_pct_threshold", 0.06)
        upper_wick_pct_max = params.get("upper_wick_pct_max", 0.02)
        vol_multiple = params.get("vol_multiple", 2.5)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 2:
                continue
            latest = hist.row(-1, named=True)
            if latest["open"] is None or latest["close"] is None or latest["high"] is None:
                continue
            if latest["open"] <= 0 or latest["close"] <= 0 or latest["high"] <= 0:
                continue
            up_pct = (latest["close"] - latest["open"]) / latest["open"]
            if up_pct < up_pct_threshold:
                continue
            upper_wick = latest["high"] - latest["close"]
            upper_wick_pct = upper_wick / latest["close"]
            if upper_wick_pct > upper_wick_pct_max:
                continue
            prev = hist.row(-2, named=True)
            if prev["volume"] is None or prev["volume"] <= 0:
                continue
            if latest["volume"] / prev["volume"] < vol_multiple:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
