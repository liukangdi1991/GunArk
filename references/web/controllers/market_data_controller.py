from __future__ import annotations

from typing import Any

from web.services import market_service


def get_market_data_status() -> dict[str, Any]:
    return market_service.get_market_data_status()


def list_trading_dates(from_: str | None = None, to: str | None = None) -> dict[str, Any]:
    return market_service.list_trading_dates(from_=from_, to=to)
