"""超跌反弹: 在超跌抄底（C1/C2/C3）基础上，新增「近 6 日中前 5 日 MT 连续下行（绿柱）、当日回升（红柱）」。

TDX 语义:
  C1 = MT 振荡器转升（当日 MT > 昨日 MT，且此前在回落，反弹力度 ≥ 前期跌幅）
  C2 = DD2 = C - 2*REF(C,1) + REF(C,2) > 0 且 DD2 = HHV(DD2,5)
  C3 = ZXK < DKK（双线下方）
  NEW = 近 6 个交易日中，前 5 个交易日的 MT 逐日下行（绿柱），当日 MT 回升（红柱）
  选股 = C1 AND C2 AND C3 AND NEW

未包含 C4（收盘 < ZXK）和 C5（MACD DIF 拐头）。
"""

from __future__ import annotations

import time

import polars as pl

from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)


class OversoldReboundSelector(SelectionStrategy):
    """超跌反弹：MT 连续 5 日绿柱后当日转红，叠加短期线在多空线下方 +
    收盘二阶差分创新高——捕捉急跌后动能首次翻转的反弹起点。"""

    REQUIRES_MARKET_CAP = False

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        if market_data.is_empty():
            return WarmupResult(grouped={})
        from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines_adjusted
        p = self.definition.default_params
        _, dkk = compute_zx_lines_adjusted(
            market_data,
            m1=p.get("m1", 14), m2=p.get("m2", 28),
            m3=p.get("m3", 57), m4=p.get("m4", 114),
        )
        ema1 = p.get("ema1", 10)
        zxk_alpha = 2.0 / (ema1 + 1)
        diff_fast = p.get("dif_fast", 12)
        diff_slow = p.get("dif_slow", 26)
        parts = []
        for g in market_data.partition_by("code"):
            close = g["close"]
            mt = compute_mt(
                g["high"], g["low"], close,
                n=p.get("n", 4), m=p.get("m", 6), t=p.get("t", 4),
            )
            zxk = close.ewm_mean(alpha=zxk_alpha, adjust=False).ewm_mean(alpha=zxk_alpha, adjust=False)
            diff = (close.ewm_mean(alpha=2.0 / (diff_fast + 1), adjust=False)
                    - close.ewm_mean(alpha=2.0 / (diff_slow + 1), adjust=False))
            dd2 = close - 2 * close.shift(1) + close.shift(2)
            parts.append(g.with_columns([
                mt, zxk.alias("zxk"), diff.alias("diff"), dd2.alias("dd2"),
            ]))
        df = pl.concat(parts).with_columns([dkk.alias("dkk")])
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        p = self.definition.default_params
        m4 = p.get("m4", 114)
        dd2_window = p.get("dd2_window", 5)
        selected = []
        for code, hist in warmup.grouped.items():
            if len(hist) < m4 + 1:
                continue
            latest = hist.row(-1, named=True)
            if any(v is None or v != v for v in
                   (latest["mt"], latest["zxk"], latest["diff"],
                    latest["dd2"], latest["dkk"], latest["close"])):
                continue

            mt = hist["mt"].to_list()

            # C1：MT 转升（当日 > 昨日，且此前在回落，反弹力度 ≥ 前期跌幅）
            mt_t, mt_1, mt_2 = mt[-1], mt[-2], mt[-3]
            if not (mt_t > mt_1 and mt_1 < mt_2 and mt_t - mt_1 >= mt_2 - mt_1):
                continue

            # C2：DD2 条件
            dd2 = hist["dd2"].to_list()
            dd2_t = dd2[-1]
            if not (dd2_t > 0 and dd2_t == max(dd2[-dd2_window:])):
                continue

            # C3：双线下方
            if not (latest["zxk"] < latest["dkk"]):
                continue

            # NEW：近 6 日中，前 5 天 MT 逐日下行（绿柱），当日 MT 回升（红柱）
            # MT 值序列（最近 6 根）：v0 ≥ v1 ≥ … ≥ v4 且 v5 > v4
            mt_6 = mt[-6:]
            if len(mt_6) < 6:
                continue
            if not all(mt_6[i] > mt_6[i + 1] for i in range(5)):
                continue  # 不满足绿柱序列

            selected.append(code)
        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=selected,
            elapsed_seconds=time.time() - t0,
        )
