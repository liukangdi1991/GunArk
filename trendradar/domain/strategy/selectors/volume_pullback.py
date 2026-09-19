"""放量回踩（趋势线/多空线）——基于 B1 骨架的贴线选股。

两策略共用同一骨架（用户 2026-09-18 确认）：
  流通市值 ≥ 50 亿
  短期线（DUAN_QI=EMA(EMA(C,10),10)）> 多空线（(MA14+MA28+MA57+MA114)/4）
  20 日内存在倍量柱（VOL > 前日 VOL×2）
  |收盘 − 目标线| / 目标线 < 2%（贴线）
  收盘 ≥ 目标线
目标线：放量回踩趋势线=短期线；放量回踩多空线=多空线。

与 B1 复用同一公式层（compute_b1_columns 的中间列），指标同用前复权价——
贴线比较的收盘与目标线同为前复权口径，除权窗口内不失真。
"""

import time

import polars as pl

from trendradar.domain.strategy.formulas.b1 import compute_b1_columns
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)

PULLBACK_MAX_DEV = 0.02  # 贴线阈值：|收盘−目标线|/目标线 < 2%


class _VolumePullbackBase(SelectionStrategy):
    """子类只需指定 LINE（贴哪条线）与 strategy_id/name。"""

    REQUIRES_MARKET_CAP = True
    LINE = "duan_qi"  # "duan_qi"=短期趋势线；"duo_kong"=多空线

    def __init__(self, definition):
        self.definition = definition

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        if market_data.is_empty():
            return WarmupResult(grouped={})
        params = self.definition.default_params
        m_windows = (
            int(params.get("m1", 14)),
            int(params.get("m2", 28)),
            int(params.get("m3", 57)),
            int(params.get("m4", 114)),
        )
        df = compute_b1_columns(market_data, m_windows=m_windows)
        grouped = {g["code"][0]: g for g in df.partition_by("code")}
        return WarmupResult(grouped=grouped)

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        t0 = time.time()
        params = self.definition.default_params
        # 公式：流通市值:=FINANCE(40)/100000000; 流通市值>=50（亿元）
        mv_min_wan = float(params.get("mv_min_yi", 50)) * 10000.0
        market_cap = context.market_cap or {}
        line_col = f"_b1_{self.LINE}"

        selected = []
        for code, hist in warmup.grouped.items():
            if hist.is_empty():
                continue
            latest = hist.row(-1, named=True)
            duan = latest.get("_b1_duan_qi")
            duo = latest.get("_b1_duo_kong")
            close = latest.get("_b1_close_qfq")
            line = latest.get(line_col)
            if None in (duan, duo, close, line) or duo == 0 or line == 0:
                continue
            if not duan > duo:
                continue  # 短期线必须在多空线上方（趋势结构）
            if not latest.get("_b1_blz_exist"):
                continue  # 20 日内无倍量柱
            if not close >= line:
                continue  # 回踩必须收在线上
            if abs(close - line) / line >= PULLBACK_MAX_DEV:
                continue  # 贴线：偏离 < 2%
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


class VolumePullbackTrendSelector(_VolumePullbackBase):
    """放量回踩趋势线：贴短期趋势线（DUAN_QI）。"""

    LINE = "duan_qi"


class VolumePullbackBullBearSelector(_VolumePullbackBase):
    """放量回踩多空线：贴多空线（DUO_KONG）。"""

    LINE = "duo_kong"
