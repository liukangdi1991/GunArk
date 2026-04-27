from __future__ import annotations

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    strategies: list[str] | None = None
    mode: str = "unlimited_cash"
    cash_per_trade: float = 50_000.0
    run_name: str | None = None
