import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.bbi import compute_bbi, bbi_deriv_uptrend
from trendradar.domain.strategy.formulas.kdj import compute_kdj_grouped
from trendradar.domain.strategy.formulas.ma import compute_ma, compute_dif_grouped
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class BBIKDJSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        j_series = compute_kdj_grouped(market_data)
        bbi_series = compute_bbi(market_data)
        dif_series = compute_dif_grouped(market_data)
        ma60_series = compute_ma(market_data, 60)
        short_line, long_line = compute_zx_lines(market_data)
        df = market_data.with_columns([
            j_series.alias("j"), bbi_series.alias("bbi"), dif_series.alias("dif"),
            ma60_series.alias("ma_60"), short_line.alias("short_term_trend_line"),
            long_line.alias("long_term_bull_bear_line"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        j_threshold = params.get("j_threshold", 15)
        bbi_min_window = params.get("bbi_min_window", 20)
        max_window = params.get("max_window", 120)
        bbi_q_threshold = params.get("bbi_q_threshold", 0.2)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < max_window:
                continue
            bbi_vals = hist["bbi"].drop_nulls()
            if len(bbi_vals) < max_window:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["dif"] is None or latest["dif"] <= 0:
                continue
            if bbi_deriv_uptrend(bbi_vals, bbi_min_window, max_window, bbi_q_threshold):
                selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
