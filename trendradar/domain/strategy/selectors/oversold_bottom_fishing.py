"""超跌抄底: MT 转升 + 收盘二阶差分新高 + 双线下方超跌 + MACD DIF 拐头.

TDX 公式:
  C1 = RED AND REF(GREEN,1) AND RED_H >= REF(GREEN_H,1)   (MT 振荡器转升)
  DD2 = C - 2*REF(C,1) + REF(C,2);  C2 = DD2>0 AND DD2=HHV(DD2,5)
  ZXK = EMA(EMA(C,10),10);  DKK=(MA14+MA28+MA57+MA114)/4
  C3 = ZXK<DKK;  C4 = CLOSE<ZXK
  DIFF = EMA(C,12)-EMA(C,26)
  C5 = EVERY(DIFF<0,5) AND DIFF-REF(DIFF,1)>=0 AND REF(EVERY(DIFF-REF(DIFF,1)<0,4),1)
  XG = C1 AND C2 AND C3 AND C4 AND C5
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class OversoldBottomFishingSelector(SelectionStrategy):
    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines
        _, dkk = compute_zx_lines(market_data)  # 四线平均（扁平列；判定行 MA114 窗口在股内）
        # ewm/位移类指标必须按 code 分组——扁平列会跨股票污染（SMA/EMA 递归 + shift）
        parts = []
        for g in market_data.partition_by("code"):
            close = g["close"]
            mt = compute_mt(g["high"], g["low"], close)
            zxk = close.ewm_mean(alpha=2 / 11, adjust=False).ewm_mean(alpha=2 / 11, adjust=False)
            diff = (close.ewm_mean(alpha=2 / 13, adjust=False)
                    - close.ewm_mean(alpha=2 / 27, adjust=False))
            dd2 = close - 2 * close.shift(1) + close.shift(2)
            parts.append(g.with_columns([
                mt, zxk.alias("zxk"), diff.alias("diff"), dd2.alias("dd2"),
            ]))
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
            # null/NaN 一律排除（0-span 一字板等）
            if any(v is None or v != v for v in
                   (latest["mt"], latest["zxk"], latest["diff"],
                    latest["dd2"], latest["dkk"], latest["close"])):
                continue
            mt = hist["mt"].to_list()
            mt_t, mt_1, mt_2 = mt[-1], mt[-2], mt[-3]
            if not (mt_t > mt_1 and mt_1 < mt_2 and mt_t - mt_1 >= mt_2 - mt_1):
                continue  # C1
            dd2 = hist["dd2"].to_list()
            dd2_t = dd2[-1]
            if not (dd2_t > 0 and dd2_t == max(dd2[-5:])):
                continue  # C2
            if not (latest["zxk"] < latest["dkk"]):
                continue  # C3
            if not (latest["close"] < latest["zxk"]):
                continue  # C4
            diff = hist["diff"].to_list()
            if not (all(d < 0 for d in diff[-5:])          # EVERY(DIFF<0,5) 含今日
                    and diff[-1] >= diff[-2]                # 今日走平/回升（仍为负）
                    and diff[-2] < diff[-3] and diff[-3] < diff[-4]
                    and diff[-4] < diff[-5] and diff[-5] < diff[-6]):  # 今日之前 4 日持续下行
                continue  # C5
            selected.append(code)
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
