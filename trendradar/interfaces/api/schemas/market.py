from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MarketSyncRequest(BaseModel):
    force: bool = False
    exclude_boards: list[str] = Field(default_factory=list)
    accept_partial_baseline: bool = False
    codes: list[str] | None = None


class TradingDatesResponse(BaseModel):
    dates: list[str] = Field(default_factory=list)
