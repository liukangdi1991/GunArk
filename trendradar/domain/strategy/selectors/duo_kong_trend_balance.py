"""多空趋势平衡: 近10个交易日（含当日）**每天都**满足 短期线贴多空线（偏离<0.3%），
且当日成交量排近10日倒数第3以内（地量确认），+ 流通市值≥50亿。

TDX 公式:
  知行短期趋势线 := EMA(EMA(C,10),10)
  知行多空线 := (MA(CLOSE,M1)+MA(CLOSE,M2)+MA(CLOSE,M3)+MA(CLOSE,M4))/4
                (M1=14 M2=28 M3=57 M4=114)
  当日条件 := ABS(知行短期趋势线 - 知行多空线) / 知行多空线 < 0.003
  满足条件天数 := COUNT(当日条件, 10)
  选股条件 : 满足条件天数 = 10（全部满足） AND 当日量排倒数第3 AND 流通市值 ≥ 50
  （公式中的 MA1=MA(C,60)、MA2=EMA(C,13) 未参与选股条件，未实现。）

COUNT 含当日（通达信语义）。多空线 ≤ 0 或空值的交易日不参与判定。
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
    """多空趋势平衡：短期线与多空线连续 10 日贴合（收敛 ≤ 0.3%），
    成交量缩到近 10 日倒数 3 以内——多空方向未选边 + 量能枯竭，变盘前夜。"""

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
        threshold = float(params.get("balance_threshold", 0.003))
        mv_min_wan = float(params.get("circ_mv_min_yi", 50)) * 10000.0  # 亿→万元
        market_cap = context.market_cap or {}

        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < window:
                continue
            tail = hist.tail(window)
            shorts = tail["zx_short"].to_list()
            longs = tail["zx_long"].to_list()
            vols = tail["volume"].to_list()

            # ① 近 10 日每天都满足 |短-长|/长 < 阈值（有一条不满足即整只票不选）
            if not _all_converged(shorts, longs, threshold):
                continue

            # ② 今日成交量排近 10 日倒数第 3 以内（地量确认）
            today_vol = vols[-1] if vols else None
            if today_vol is None or today_vol <= 0:
                continue
            if sum(1 for v in vols if v is not None and v > 0 and v < today_vol) >= 3:
                continue

            # ③ 流通市值 ≥ 50 亿
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


def _all_converged(shorts: list[float | None], longs: list[float | None],
                   threshold: float) -> bool:
    """近 window 根 K 线每天都满足 |短-长|/长 < 阈值。多空线 ≤ 0 或空值不满足。"""
    for short_val, long_val in zip(shorts, longs):
        if long_val is None or long_val <= 0 or short_val is None:
            return False
        if abs(short_val - long_val) / long_val >= threshold:
            return False
    return True
