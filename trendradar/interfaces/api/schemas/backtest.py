from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BacktestResultItem(BaseModel):
    execution_key: str
    job_id: str
    created_at: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class BacktestResultsListResponse(BaseModel):
    items: list[BacktestResultItem] = Field(default_factory=list)
