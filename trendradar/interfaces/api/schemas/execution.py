from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExecutionRequest(BaseModel):
    type: str | None = None
    params: dict[str, Any] | None = None
    start_date: str | None = None
    end_date: str | None = None
    codes: list[str] | None = None
    groups: list[str] | None = None
    strategies: list[str] | None = None
    boards: list[str] | None = None


class DeleteKeysRequest(BaseModel):
    execution_keys: list[str] | None = None


class SelectionBacktestRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    codes: list[str] | None = None
    groups: list[str] | None = None
    strategies: list[str] | None = None
    boards: list[str] | None = None
    backtest: dict[str, Any] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    execution_key: str = Field(..., min_length=1)
    capital: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    costs: dict[str, Any] = Field(default_factory=dict)
    portfolio: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    request: dict | None = None
    result: dict | None = None
    error: str | None = None
