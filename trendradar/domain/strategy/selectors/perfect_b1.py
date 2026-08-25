import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj_grouped


class PerfectB1Selector(SelectionStrategy):
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
        j_threshold = params.get("j_threshold", 13)
        amplitude_limit = params.get("amplitude_limit", 0.07)
        pct_chg_upper = params.get("pct_chg_upper", 0.02)
        pct_chg_lower = params.get("pct_chg_lower", -0.02)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 2:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["high"] is None or latest["close"] is None or latest["close"] <= 0:
                continue
            amplitude = (latest["high"] - latest["low"]) / latest["close"]
            if amplitude > amplitude_limit:
                continue
            prev = hist.row(-2, named=True)
            if prev["close"] is None or prev["close"] <= 0:
                continue
            pct_chg = latest["close"] / prev["close"] - 1
            if pct_chg > pct_chg_upper or pct_chg < pct_chg_lower:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
