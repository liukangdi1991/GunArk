from __future__ import annotations

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    strategies: list[str] | None = None
    mode: str = "unlimited_cash"
    cash_per_trade: float = 50_000.0
    run_name: str | None = None


class BacktestFromSelectionRequest(BaseModel):
    selection_execution_keys: list[str]
    strategies: list[str] | None = None
    mode: str = "unlimited_cash"
    cash_per_trade: float = 50_000.0
    run_name: str | None = None


class SelectionBacktestRequest(BaseModel):
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    strategies: list[str] | None = None
    tickers: list[str] | None = None
    mode: str = "unlimited_cash"
    cash_per_trade: float = 50_000.0
    run_name: str | None = None


class DeleteBacktestResultsRequest(BaseModel):
    execution_keys: list[str] | None = None
