"""个股维度只读路由（N9：身份用 path）。"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request as FastAPIRequest

from trendradar.app.services.kline_service import get_kline, get_stock_snapshot
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    MarketDataUnavailable,
)
from trendradar.interfaces.api.schemas.market import KlineResponse

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

_PERIODS = {p.value for p in KlinePeriod}
_ADJUSTS = {a.value for a in AdjustMode}


def _num(value: Any) -> float | None:
    """N6：NaN/±Inf 归一为 null（FastAPI 默认序列化 NaN 会产出非法 JSON）。"""
    if value is None:
        return None
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _timestamp(d: date) -> int:
    """M11+N19：Asia/Shanghai 午夜毫秒，前端零时区转换。"""
    return int(
        datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000
    )


@router.get("/{code}/snapshot")
def get_stock_snapshot_route(
    request: FastAPIRequest,
    code: str = Path(pattern=r"^\d{6}$"),
) -> Any:
    """最新交易日个股快照（流通市值/换手率等；无 token/接口异常时字段为 null）。"""
    from trendradar.infrastructure.tushare.client import get_pro

    try:
        pro = get_pro()
    except Exception:
        pro = None
    return get_stock_snapshot(request.app.state.market_store, code, pro)


@router.get("/{code}/kline", response_model=KlineResponse)
def get_stock_kline(
    request: FastAPIRequest,
    code: str = Path(pattern=r"^\d{6}$"),  # F3：path 参数用 Path 校验（禁 int：000001→1）
    period: str | None = Query(default=None),
    adjust: str | None = Query(default=None),
) -> Any:
    period_value = (period or KlinePeriod.DAILY.value).lower()
    adjust_value = (adjust or AdjustMode.QFQ.value).lower()
    if period_value not in _PERIODS:
        raise HTTPException(status_code=422, detail=f"非法 period: {period}")
    if adjust_value not in _ADJUSTS:
        raise HTTPException(status_code=422, detail=f"非法 adjust: {adjust}")

    market_store = request.app.state.market_store
    try:
        series = get_kline(
            market_store, code, KlinePeriod(period_value), AdjustMode(adjust_value)
        )
    except BarsUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    bars_df = series.bars
    bars = [
        {
            "timestamp": _timestamp(row["date"]),
            "date": row["date"].isoformat(),
            "open": _num(row["open"]),
            "high": _num(row["high"]),
            "low": _num(row["low"]),
            "close": _num(row["close"]),
            "pre_close": _num(row["pre_close"]),
            "volume": _num(row["volume"]),
            "amount": _num(row["amount"]),
            "zx_short": _num(row["zx_short"]),
            "zx_long": _num(row["zx_long"]),
        }
        for row in bars_df.iter_rows(named=True)
    ]
    return KlineResponse(
        code=code,
        name=series.name,
        industry=series.industry,
        period=period_value,
        adjust=adjust_value,
        adjust_degraded=series.adjust_degraded,
        last_bar_date=bars_df["date"][-1].isoformat(),
        bars=bars,
    )
