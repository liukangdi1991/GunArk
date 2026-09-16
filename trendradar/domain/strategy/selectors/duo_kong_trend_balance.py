"""多空趋势平衡: 近10日内 ≥5 日 短期线贴合多空线（偏离 < 0.3%）+ 流通市值 ≥ 50亿.

TDX 公式:
  知行短期趋势线 := EMA(EMA(C,10),10)
  知行多空线 := (MA(CLOSE,M1)+MA(CLOSE,M2)+MA(CLOSE,M3)+MA(CLOSE,M4))/4
                (M1=14 M2=28 M3=57 M4=114)
  当日条件 := ABS(知行短期趋势线 - 知行多空线) / 知行多空线 < 0.003
  满足条件天数 := COUNT(当日条件, 10)
  流通市值 := FINANCE(40)/100000000
  选股条件 : 满足条件天数 >= 5 AND 流通市值 >= 50
  （公式中的 MA1=MA(C,60)、MA2=EMA(C,13) 未参与选股条件，未实现。）

COUNT 含当日（通达信语义）。多空线 ≤ 0 或空值的交易日不参与计数。
两条线复用 zxdkx 公式（前复权口径，与图表一致）。
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines_adjusted
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class DuoKongTrendBalanceSelector(SelectionStrategy):
    """多空趋势平衡：短期线与多空线长时间贴合（收敛 ≤ 0.3%），多空方向未选边——
    等待变盘的「平衡」形态。"""

    REQUIRES_MARKET_CAP = True

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        short_line, long_line = compute_zx_lines_adjusted(market_data)
        df = market_data.with_columns([
            short_line.alias("zx_short"),
            long_line.alias("zx_long"),
        ])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        window = int(params.get("analysis_window", 10))
        min_days = int(params.get("min_balance_days", 5))
        threshold = float(params.get("balance_threshold", 0.003))
        mv_min_wan = float(params.get("circ_mv_min_yi", 50)) * 10000.0  # 亿→万元
        market_cap = context.market_cap or {}

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < min_days:
                continue
            tail = hist.tail(window)
            if count_balance_days(tail["zx_short"].to_list(), tail["zx_long"].to_list(), threshold) < min_days:
                continue
            mv = market_cap.get(code)
            if mv is None or mv < mv_min_wan:
                continue
            selected.append(code)

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )


def count_balance_days(short_values: list[float | None], long_values: list[float | None],
                       threshold: float) -> int:
    """COUNT(ABS(短-长)/长 < 阈值, n)：含当日；多空线 ≤ 0 或空值的交易日不计数。"""
    count = 0
    for short_val, long_val in zip(short_values, long_values):
        if long_val is None or long_val <= 0 or short_val is None:
            continue
        if abs(short_val - long_val) / long_val < threshold:
            count += 1
    return count
