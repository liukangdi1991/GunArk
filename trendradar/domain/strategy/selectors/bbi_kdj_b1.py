import time
import polars as pl
from trendradar.domain.strategy.protocol import SelectionStrategy, SelectionContext, SelectionResult
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_ma, compute_dif
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class BBIKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def select(self, context: SelectionContext) -> SelectionResult:
        t0 = time.time()
        df = context.market_data
        params = self.definition.default_params

        j_threshold = params.get("j_threshold", 15)
        bbi_min_window = params.get("bbi_min_window", 20)
        max_window = params.get("max_window", 120)
        bbi_q_threshold = params.get("bbi_q_threshold", 0.2)

        if df.is_empty():
            return SelectionResult(
                strategy_id=self.definition.strategy_id,
                strategy_name=self.definition.name,
                trade_date=context.trade_date,
                selected_codes=[],
                elapsed_seconds=time.time() - t0,
            )

        _, _, j_series = compute_kdj(df)
        bbi_series = compute_bbi(df)
        dif_series = compute_dif(df)
        ma60_series = compute_ma(df, 60)
        short_line, long_line = compute_zx_lines(df)

        df = df.with_columns([
            bbi_series,
            j_series,
            dif_series,
            ma60_series,
            short_line,
            long_line,
        ])

        last_per_code = df.group_by("code").agg([
            pl.col("j").last(),
            pl.col("bbi").last(),
            pl.col("dif").last(),
            pl.col("ma_60").last(),
            pl.col("short_term_trend_line").last(),
            pl.col("long_term_bull_bear_line").last(),
            pl.col("close").last(),
        ])

        filtered = last_per_code.filter(
            (pl.col("j") < j_threshold)
            & (pl.col("dif") > 0)
        )

        selected = []
        for code in filtered["code"].to_list():
            hist = df.filter(pl.col("code") == code).sort("date")
            if len(hist) < max_window:
                continue
            bbi_vals = hist["bbi"].drop_nulls()
            if len(bbi_vals) < max_window:
                continue
            if bbi_deriv_uptrend(bbi_vals, bbi_min_window, max_window, bbi_q_threshold):
                selected.append(code)

        elapsed = time.time() - t0
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=elapsed,
        )
