import time
import polars as pl
from trendradar.domain.strategy.protocol import SelectionStrategy, SelectionContext, SelectionResult
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.ma import compute_ma


class BBIShortLongSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def select(self, context: SelectionContext) -> SelectionResult:
        t0 = time.time()
        df = context.market_data
        params = self.definition.default_params

        n_short = params.get("n_short", 5)
        n_long = params.get("n_long", 21)
        bbi_min_window = params.get("bbi_min_window", 2)
        max_window = params.get("max_window", 120)

        if df.is_empty():
            return SelectionResult(
                strategy_id=self.definition.strategy_id,
                strategy_name=self.definition.name,
                trade_date=context.trade_date,
                selected_codes=[],
                elapsed_seconds=time.time() - t0,
            )

        bbi_series = compute_bbi(df)

        df = df.with_columns([
            bbi_series,
            compute_ma(df, n_short),
            compute_ma(df, n_long),
        ])

        ma_short_name = f"ma_{n_short}"
        ma_long_name = f"ma_{n_long}"

        codes = df["code"].unique().to_list()
        selected = []
        for code in codes:
            hist = df.filter(pl.col("code") == code).sort("date")
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

        elapsed = time.time() - t0
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=elapsed,
        )
