import time
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)
from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_ma


class MA60VolumeWaveSelector(SelectionStrategy):
    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        _, _, j_series = compute_kdj(market_data)
        ma60_series = compute_ma(market_data, 60)
        df = market_data.with_columns([j_series.alias("j"), ma60_series.alias("ma_60")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        lookback_n = params.get("lookback_n", 25)
        vol_multiple = params.get("vol_multiple", 1.8)
        j_threshold = params.get("j_threshold", 15)

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < lookback_n + 1:
                continue
            latest = hist.row(-1, named=True)
            if latest["j"] is None or latest["j"] >= j_threshold:
                continue
            if latest["close"] is None or latest["ma_60"] is None:
                continue
            if latest["close"] <= latest["ma_60"]:
                continue
            recent = hist.slice(-lookback_n, lookback_n)
            avg_vol = recent["volume"].mean()
            if avg_vol is None or avg_vol <= 0:
                continue
            if latest["volume"] <= avg_vol * vol_multiple:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
