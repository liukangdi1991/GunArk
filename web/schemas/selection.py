from __future__ import annotations

from pydantic import BaseModel, Field


class SelectionRequest(BaseModel):
    date: str | None = None
    strategies: list[str] | None = None
    tickers: list[str] | None = None


class BatchSelectionRequest(BaseModel):
    month: str | None = None
    from_date: str | None = Field(default=None, alias="from")
    to: str | None = None
    strategies: list[str] | None = None


class DeleteSelectionResultsRequest(BaseModel):
    execution_keys: list[str] | None = None
