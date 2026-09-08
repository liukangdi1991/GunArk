"""个股K线读服务：404/503 判别 + meta 容错 + 域对象组装（spec §4.1）。"""
from __future__ import annotations

from datetime import date

import polars as pl

from trendradar.domain.market.data_store import LocalParquetMarketStore
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    KlineSeries,
    MarketDataUnavailable,
    build_kline_series,
)


def _lookup_meta(market_store: LocalParquetMarketStore, code: str) -> tuple[str, str | None]:
    """stock_meta 容错读取（M9）：缺文件/缺列视同查无此股，不得按列直接索引。"""
    try:
        meta = market_store.stock_meta([code])
    except Exception:
        return code, None
    if meta.is_empty() or "name" not in meta.columns:
        return code, None
    row = meta.row(0, named=True)
    name = row.get("name") or code
    industry = row.get("industry") if "industry" in meta.columns else None
    return str(name), industry


def get_kline(
    market_store: LocalParquetMarketStore,
    code: str,
    period: KlinePeriod,
    adjust: AdjustMode,
) -> KlineSeries:
    """读单股全量日线并产出周期序列。

    404/503 判别机制（F2：get_rows 对缺失文件静默返空帧——data_store.py:186-187
    实证——不钉机制则 503 不可达）：
    ① bars_dir 缺失 → MarketDataUnavailable（503）；
    ② get_rows 读异常（OSError/parquet 解析错）→ MarketDataUnavailable（503）；
    ③ 空帧 → BarsUnavailable（404，文件缺失与 0 行同语义）。
    """
    bars_dir = market_store.bars_dir
    if not bars_dir.is_dir():
        raise MarketDataUnavailable("行情数据正在更新，请稍后重试")
    try:
        raw = market_store.get_rows(code, date(1990, 1, 1), date.today())
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise MarketDataUnavailable("行情数据读取失败，请稍后重试") from exc
    if raw.is_empty():
        raise BarsUnavailable("无该股行情数据")
    bars, degraded = build_kline_series(raw, period, adjust)
    name, industry = _lookup_meta(market_store, code)
    return KlineSeries(bars=bars, adjust_degraded=degraded, name=name, industry=industry)


def get_stock_snapshot(market_store, code: str, pro) -> dict:
    """最新交易日个股快照（流通市值/换手率/PE/PB，daily_basic）。

    无 token 或接口异常时字段为 null（前端显示「—」），不阻塞 K线主数据。
    """
    try:
        calendar = market_store.get_calendar()
    except Exception:
        calendar = []
    trade_date = calendar[-1] if calendar else date.today()
    snapshot: dict = {
        "code": code,
        "trade_date": trade_date.isoformat(),
        "circ_mv": None,
        "total_mv": None,
        "turnover_rate": None,
        "pe_ttm": None,
        "pb": None,
    }
    if pro is None:
        return snapshot
    try:
        from trendradar.infrastructure.tushare.market_cap import daily_basic_snapshot

        entry = daily_basic_snapshot(pro, trade_date).get(code)
    except Exception:
        return snapshot
    if entry:
        snapshot.update(entry)
    return snapshot
