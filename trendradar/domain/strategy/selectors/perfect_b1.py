import time
import polars as pl
from trendradar.domain.strategy.protocol import SelectionStrategy, SelectionContext, SelectionResult
from trendradar.domain.strategy.formulas.kdj import compute_kdj


class PerfectB1Selector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def select(self, context: SelectionContext) -> SelectionResult:
        t0 = time.time()
        df = context.market_data
        params = self.definition.default_params

        j_threshold = params.get("j_threshold", 13)
        amplitude_limit = params.get("amplitude_limit", 0.07)
        pct_chg_upper = params.get("pct_chg_upper", 0.02)
        pct_chg_lower = params.get("pct_chg_lower", -0.02)

        if df.is_empty():
            return SelectionResult(
                strategy_id=self.definition.strategy_id,
                strategy_name=self.definition.name,
                trade_date=context.trade_date,
                selected_codes=[],
                elapsed_seconds=time.time() - t0,
            )

        _, _, j_series = compute_kdj(df)
        df = df.with_columns([j_series])

        codes = df["code"].unique().to_list()
        selected = []
        for code in codes:
            hist = df.filter(pl.col("code") == code).sort("date")
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

        elapsed = time.time() - t0
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=elapsed,
        )
