"""Market data status routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.market import (
    MarketSyncRequest,
    MarketStatusResponse,
)

router = APIRouter(prefix="/api", tags=["market-data"])


def _executor(request: FastAPIRequest):
    return request.app.state.executor


def _store(request: FastAPIRequest):
    return request.app.state.store


@router.get("/market-data/status", response_model=MarketStatusResponse)
def get_market_status(request: FastAPIRequest):
    from trendradar.app.services.market_service import get_market_status
    return get_market_status(_store(request))


@router.post("/market-data/sync")
def submit_market_sync(body: MarketSyncRequest, request: FastAPIRequest):
    from trendradar.app.services.market_service import submit_market_sync
    try:
        job_id = submit_market_sync(
            _executor(request),
            {"codes": body.codes, "start_date": body.start_date, "end_date": body.end_date},
        )
        return {"data": {"job_id": job_id}}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/market-data/trading-dates")
def get_trading_dates(
    start: str = Query(default=None),
    end: str = Query(default=None),
):
    from datetime import date
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.domain.market.data_store import LocalParquetMarketStore

    bars_dir = runtime_root() / "storage" / "market" / "bars"
    market_store = LocalParquetMarketStore(bars_dir)

    try:
        calendar = market_store.get_calendar()
    except Exception:
        calendar = []

    all_dates = sorted(calendar)
    if start:
        s = date.fromisoformat(start)
        all_dates = [d for d in all_dates if d >= s]
    if end:
        e = date.fromisoformat(end)
        all_dates = [d for d in all_dates if d <= e]

    return {"data": {"dates": [str(d) for d in all_dates], "count": len(all_dates)}}
