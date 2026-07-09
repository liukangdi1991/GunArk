from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MarketSyncRequest(BaseModel):
    codes: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None


class MarketStatusResponse(BaseModel):
    latest_date: str | None = None
    stock_count: int = 0
    latest_sync_run: dict | None = None


class TradingDatesResponse(BaseModel):
    dates: list[str] = Field(default_factory=list)
