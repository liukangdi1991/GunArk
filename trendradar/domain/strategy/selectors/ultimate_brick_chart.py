"""极致砖型图选股: MT 绿转红(≥昨绿高) + 前3日绿柱 + 多头排列(close≥ZXK>DKK).

TDX 公式:
  C1 = RED AND REF(GREEN,1) AND RED_H >= REF(GREEN_H,1)
  C2 = REF(GREEN,1) AND REF(GREEN,2) AND REF(GREEN,3)
  ZXK = EMA(EMA(C,10),10);  DKK=(MA14+MA28+MA57+MA114)/4
  C3 = ZXK>DKK;  C4 = CLOSE>=ZXK
  XG = C1 AND C2 AND C3 AND C4
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class UltimateBrickChartSelector(SelectionStrategy):
    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines
        _, dkk = compute_zx_lines(market_data)  # 四线平均（扁平列；判定行 MA114 窗口在股内）
        # ewm 递归必须按 code 分组（禁跨股票污染）
        parts = []
        for g in market_data.partition_by("code"):
            close = g["close"]
            mt = compute_mt(g["high"], g["low"], close)
            zxk = close.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
            parts.append(g.with_columns([mt, zxk.alias("zxk")]))
        df = pl.concat(parts).with_columns([dkk.alias("dkk")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 115:   # DKK 需 MA114
                continue
            latest = hist.row(-1, named=True)
            if any(v is None or v != v for v in
                   (latest["mt"], latest["zxk"], latest["dkk"], latest["close"])):
                continue  # null/NaN 一律排除
            mt = hist["mt"].to_list()
            mt_t, mt_1, mt_2, mt_3, mt_4 = mt[-1], mt[-2], mt[-3], mt[-4], mt[-5]
            if not (mt_t > mt_1 and mt_1 < mt_2 and mt_t - mt_1 >= mt_2 - mt_1):
                continue  # C1：今日红柱、昨日绿柱、红柱高度≥昨日绿柱高度
            if not (mt_1 < mt_2 and mt_2 < mt_3 and mt_3 < mt_4):
                continue  # C2：今日之前连续 3 日绿柱
            if not (latest["zxk"] > latest["dkk"]):
                continue  # C3：短期线高于长期四线均值
            if not (latest["close"] >= latest["zxk"]):
                continue  # C4：收盘站上短期线
            selected.append(code)
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
