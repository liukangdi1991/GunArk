"""Market data status routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.market import (
    MarketSyncRequest,
)

router = APIRouter(prefix="/api", tags=["market-data"])


def _executor(request: FastAPIRequest):
    return request.app.state.executor


def _store(request: FastAPIRequest):
    return request.app.state.store


@router.get("/market-data/status")
def get_market_status(request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import market_status_payload
    return market_status_payload()


@router.post("/market-data/sync")
def submit_market_sync(body: MarketSyncRequest, request: FastAPIRequest):
    from trendradar.app.services.market_service import submit_market_sync
    try:
        job_id = submit_market_sync(
            _executor(request),
            {
                "codes": body.codes,
                "start_date": body.start_date,
                "end_date": body.end_date,
                "force": body.force,
            },
        )
        return {"data": {"job_id": job_id}}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_market_sync failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail="同步提交失败，请查看执行控制台日志")


@router.get("/market-data/trading-dates")
def get_trading_dates(
    start: str = Query(default=None),
    end: str = Query(default=None),
):
    from trendradar.interfaces.api.presenters import trading_dates_payload
    try:
        return trading_dates_payload(start, end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
