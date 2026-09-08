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


class KlineBar(BaseModel):
    timestamp: int
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    pre_close: float | None = None
    volume: float
    amount: float
    zx_short: float | None = None
    zx_long: float | None = None


class KlineResponse(BaseModel):
    code: str
    name: str
    industry: str | None = None
    period: str
    adjust: str
    adjust_degraded: bool
    last_bar_date: str
    bars: list[KlineBar]
