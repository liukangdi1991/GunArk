from __future__ import annotations

from pydantic import BaseModel


class SelectionRequest(BaseModel):
    date: str | None = None
    strategies: list[str] | None = None
    tickers: list[str] | None = None
