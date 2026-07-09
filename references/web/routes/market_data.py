from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from web.controllers import market_data_controller


router = APIRouter(prefix="/api/market-data", tags=["market-data"])


@router.get("/status")
def get_market_data_status() -> dict[str, Any]:
    return market_data_controller.get_market_data_status()


@router.get("/trading-dates")
def list_trading_dates(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> dict[str, Any]:
    return market_data_controller.list_trading_dates(from_=from_, to=to)
