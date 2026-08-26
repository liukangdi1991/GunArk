import time

import polars as pl

from trendradar.domain.strategy.formulas.super_b1 import compute_super_b1_columns
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class SuperB1Selector(SelectionStrategy):
    """SuperB1战法：通达信公式（短期线>多空线 + 振幅<7 + 涨幅<2 + 收>MA60 +
    DIF>DEA + 120日倍量 + 收盘贴多空线±1.6% + 流通市值>50亿）。"""

    # 触发 runner 按交易日拉取 daily_basic.circ_mv（万元）注入 context.market_cap
    REQUIRES_MARKET_CAP = True

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        params = self.definition.default_params
        m_windows = (
            int(params.get("m1", 14)),
            int(params.get("m2", 28)),
            int(params.get("m3", 57)),
            int(params.get("m4", 114)),
        )
        df = compute_super_b1_columns(market_data, m_windows=m_windows)
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        # 公式：流通市值:=FINANCE(40)/100000000; 流通市值>50（亿元，严格大于）
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
