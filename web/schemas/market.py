from __future__ import annotations

from pydantic import BaseModel


class FetchMarketRequest(BaseModel):
    start: str = "20190101"
    end: str = "today"
    exclude_boards: list[str] | None = None
