import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.ma import compute_ma


class BBIShortLongSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        params = self.definition.default_params
        n_short = params.get("n_short", 5)
        n_long = params.get("n_long", 21)
        df = market_data.with_columns([
            compute_bbi(market_data).alias("bbi"),
            compute_ma(market_data, n_short).alias(f"ma_{n_short}"),
            compute_ma(market_data, n_long).alias(f"ma_{n_long}"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        n_short = params.get("n_short", 5)
        n_long = params.get("n_long", 21)
        bbi_min_window = params.get("bbi_min_window", 2)
        max_window = params.get("max_window", 120)
        ma_short_name = f"ma_{n_short}"
        ma_long_name = f"ma_{n_long}"

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["close"] is None or latest["close"] <= 0:
                continue
            if latest[ma_short_name] is None or latest[ma_long_name] is None:
                continue
            if latest[ma_short_name] <= latest[ma_long_name]:
                continue
            bbi_vals = hist["bbi"].drop_nulls()
            if len(bbi_vals) < max_window:
                continue
            if not bbi_deriv_uptrend(bbi_vals, bbi_min_window, max_window):
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
