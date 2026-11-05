"""放量回踩（趋势线/多空线）——基于 B1 骨架的选股。

两策略共用骨架：流通市值≥50亿 + 短期线>多空线 + 20日内倍量柱。
价格规则 2026-11-04 拆分（用户确认口径）：
  - 放量回踩趋势线：短期趋势线 ≤ 收盘 ≤ 短期趋势线×(1+1%)——收在线上且高出不超过 1%
  - 放量回踩多空线：|收盘 − 多空线| / 多空线 ≤ 2%（对称贴线带，线上线下皆可、
    含等于；旧口径「收盘≥多空线」会排除的轻微破线回踩，新口径入选）

与 B1 复用同一公式层（compute_b1_columns 的中间列），指标同用前复权价——
贴线比较的收盘与目标线同为前复权口径，除权窗口内不失真。
"""

import time

import polars as pl

from trendradar.domain.strategy.formulas.b1 import compute_b1_columns
from trendradar.domain.strategy.protocol import (
    SelectionStrategy, SelectionContext, SelectionResult, WarmupResult,
)

PULLBACK_MAX_DEV = 0.02  # 贴线阈值：|收盘−多空线|/多空线 ≤ 2%（仅多空线版使用，含等于）
TREND_MAX_ABOVE_DEV = 0.01  # 趋势线版上限：收盘不高于短期趋势线×(1+1%)（含等于）


class _VolumePullbackBase(SelectionStrategy):
    """子类指定 LINE（目标线列）并实现 _price_ok（价格规则），其余条件共享。"""

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

        selected = []
        for code, hist in warmup.grouped.items():
            if hist.is_empty():
                continue
            latest = hist.row(-1, named=True)
            duan = latest.get("_b1_duan_qi")
            duo = latest.get("_b1_duo_kong")
            close = latest.get("_b1_close_qfq")
            if None in (duan, duo, close):
                continue
            if not duan > duo:
                continue  # 短期线必须在多空线上方（趋势结构）
            if not latest.get("_b1_blz_exist"):
                continue  # 20 日内无倍量柱
            line = latest.get(f"_b1_{self.LINE}")
            if line is None or line == 0:
                continue
            if not self._price_ok(close, line):
                continue  # 价格规则（两策略唯一分叉点，见子类）
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
    """放量回踩趋势线：短期趋势线 ≤ 收盘 ≤ 短期趋势线×(1+1%)（无下浮，破线即排除）。"""

    LINE = "duan_qi"

    @staticmethod
    def _price_ok(close, line):
        return line <= close <= line * (1 + TREND_MAX_ABOVE_DEV)


class VolumePullbackBullBearSelector(_VolumePullbackBase):
    """放量回踩多空线：|收盘 − 多空线|/多空线 ≤ 2%（对称带，线上线下皆可、含等于）。"""

    LINE = "duo_kong"

    @staticmethod
    def _price_ok(close, line):
        return abs(close - line) / line <= PULLBACK_MAX_DEV
