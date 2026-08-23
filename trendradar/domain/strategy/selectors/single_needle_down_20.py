"""单针下20: 3日随机指标下探 ≤20 + 21日区间高位 >80 + 流通市值 ≥50亿.

TDX 公式:
  短期 = 100*(C-LLV(L,3))/(HHV(C,3)-LLV(L,3))
  长期 = 100*(C-LLV(L,21))/(HHV(C,21)-LLV(L,21))
  流通市值 = FINANCE(40)/1e8 (亿); 条件 circ_mv >= 50亿 (=500000万元)
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class SingleNeedleDown20Selector(SelectionStrategy):
    REQUIRES_MARKET_CAP = True

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        params = self.definition.default_params
        n1 = int(params.get("n1", 3))
        n2 = int(params.get("n2", 21))
        c = market_data["close"]
        low = market_data["low"]
        hh3 = c.rolling_max(n1)
        ll3 = low.rolling_min(n1)
        span3 = hh3 - ll3
        # 0-span（一字板）→ null，避免 NaN 穿过 select_day 的 is-None 守卫
        short = pl.when(span3 > 0).then(100 * (c - ll3) / span3).otherwise(None)
        hh21 = c.rolling_max(n2)
        ll21 = low.rolling_min(n2)
        span21 = hh21 - ll21
        long_ = pl.when(span21 > 0).then(100 * (c - ll21) / span21).otherwise(None)
        df = market_data.with_columns([
            short.alias("short_stoch"), long_.alias("long_stoch"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        short_max = float(params.get("short_max", 20))
        long_min = float(params.get("long_min", 80))
        circ_mv_min = float(params.get("circ_mv_min_yi", 50)) * 10000.0  # 亿→万元

        selected = []
        market_cap = context.market_cap or {}
        for code, hist in warmup.grouped.items():
            latest = hist.row(-1, named=True)
            if latest["short_stoch"] is None or latest["long_stoch"] is None:
                continue
            if latest["short_stoch"] > short_max:
                continue
            if latest["long_stoch"] <= long_min:
                continue
            mv = market_cap.get(code)
            if mv is None or mv < circ_mv_min:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
