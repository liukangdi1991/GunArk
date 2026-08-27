import time

import polars as pl

from trendradar.domain.strategy.formulas.ma_convergence import compute_macd_ma_convergence_columns
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class MacdMaConvergenceSelector(SelectionStrategy):
    """MACD均线粘合：MA34/55/144/233 粘合形态（短间距 2-6%、长线多头）+
    收盘在 MA55-MA144 之间 + MACD DIF>0 + 流通市值>50亿。指标前复权。"""

    # 触发 runner 按交易日拉取 daily_basic.circ_mv（万元）注入 context.market_cap
    REQUIRES_MARKET_CAP = True

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        df = compute_macd_ma_convergence_columns(market_data)
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        # 公式 COND8：FINANCE(40)/100000000>50（流通市值>50 亿，严格大于）
        mv_min_wan = float(params.get("mv_min_yi", 50)) * 10000.0
        market_cap = context.market_cap or {}

        selected = []
        for code, hist in warmup.grouped.items():
            if hist.is_empty():
                continue
            latest = hist.row(-1, named=True)
            if not latest.get("_b1_signal"):
                continue
            mv = market_cap.get(code)
            if mv is None or mv <= mv_min_wan:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
