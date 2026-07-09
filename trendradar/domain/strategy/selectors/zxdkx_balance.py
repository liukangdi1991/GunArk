import time
import polars as pl
from trendradar.domain.strategy.protocol import SelectionStrategy, SelectionContext, SelectionResult
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines, zx_stick_ratio, zx_stick_condition


class ZXDKXBalanceSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def select(self, context: SelectionContext) -> SelectionResult:
        t0 = time.time()
        df = context.market_data
        params = self.definition.default_params

        zx_stick_limit_threshold = params.get("zx_stick_limit_threshold", 0.04)
        close_vs_long_term_bull_bear_line_limit_threshold = params.get(
            "close_vs_long_term_bull_bear_line_limit_threshold", 0.95
        )

        if df.is_empty():
            return SelectionResult(
                strategy_id=self.definition.strategy_id,
                strategy_name=self.definition.name,
                trade_date=context.trade_date,
                selected_codes=[],
                elapsed_seconds=time.time() - t0,
            )

        short_line, long_line = compute_zx_lines(df)
        df = df.with_columns([short_line, long_line])

        codes = df["code"].unique().to_list()
        selected = []
        for code in codes:
            hist = df.filter(pl.col("code") == code).sort("date")
            if len(hist) < 114:
                continue

            latest = hist.row(-1, named=True)
            long_line_val = latest["long_term_bull_bear_line"]
            if long_line_val is None or long_line_val <= 0:
                continue

            close_to_long = latest["close"] / long_line_val
            if close_to_long > close_vs_long_term_bull_bear_line_limit_threshold:
                continue

            stick_cond_series = zx_stick_condition(
                hist["short_term_trend_line"],
                hist["long_term_bull_bear_line"],
                zx_stick_limit_threshold,
            )
            if len(stick_cond_series) == 0 or stick_cond_series[-1] is None:
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
