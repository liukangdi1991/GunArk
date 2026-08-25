"""砖型图: MT 振荡器转升 + 前 3 日绿柱 + 收盘站上四线均值.

TDX 公式:
  VAR1A=(HHV(H,4)-C)/(HHV(H,4)-LLV(L,4))*100-90
  VAR2A=SMA(VAR1A,4,1)+100;  VAR3A=(C-LLV(L,4))/(HHV(H,4)-LLV(L,4))*100
  VAR4A=SMA(VAR3A,6,1);      VAR5A=SMA(VAR4A,6,1)+100
  VAR6A=VAR5A-VAR2A;         MT=MAX(VAR6A-4, 0)
  RED=MT>REF(MT,1); GREEN=MT<REF(MT,1)
  C1=RED AND REF(GREEN,1) AND (MT-REF(MT,1))>=(REF(MT,1)-REF(MT,2))
  C2=REF(GREEN,1) AND REF(GREEN,2) AND REF(GREEN,3)
  DKK=(MA14+MA28+MA57+MA114)/4;  C3=C>=DKK
  XG=C1 AND C2 AND C3

SMA(X,N,M) 为通达信中国式递归平滑，M=1 时等价 ewm(alpha=1/N, adjust=False)。
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class BrickChartSelector(SelectionStrategy):
    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines
        p = self.definition.default_params
        _, long_line = compute_zx_lines(
            market_data,
            m1=p.get("m1", 14), m2=p.get("m2", 28),
            m3=p.get("m3", 57), m4=p.get("m4", 114),
        )  # 四线平均 = DKK（扁平列；判定行窗口在股内，边界无影响）
        # MT 是 SMA 递归，必须按 code 分组计算——扁平列会把上一只股票的 ewm 状态带进来
        # （runner 保证 market_data 按 [code, date] 排序，partition/concat 不改变行序，dkk 对齐安全）
        parts = []
        for g in market_data.partition_by("code"):
            mt = compute_mt(
                g["high"], g["low"], g["close"],
                n=p.get("n", 4), m=p.get("m", 6), t=p.get("t", 4),
            )
            parts.append(g.with_columns([mt]))
        df = pl.concat(parts)
        df = df.with_columns([long_line.alias("dkk")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < 5:   # 需要 5 个 MT 值（row(-5)）
                continue
            mt = hist["mt"]
            last5 = [mt[-1], mt[-2], mt[-3], mt[-4], mt[-5]]
            if any(v is None for v in last5):
                continue
            mt_t, mt_1, mt_2, mt_3, mt_4 = last5
            red = mt_t > mt_1
            green1 = mt_1 < mt_2
            green2 = mt_2 < mt_3
            green3 = mt_3 < mt_4
            red_h = mt_t - mt_1
            green_h1 = mt_2 - mt_1  # GREEN_H=REF(MT,1)-MT → 昨日跌幅

            c1 = red and green1 and red_h >= green_h1
            c2 = green1 and green2 and green3
            latest = hist.row(-1, named=True)
            # dkk 由 compute_zx_lines 生成，历史不足 114 行时为 None → 自动排除
            if latest["dkk"] is None or latest["close"] is None:
                continue
            c3 = latest["close"] >= latest["dkk"]
            if c1 and c2 and c3:
                selected.append(code)
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
