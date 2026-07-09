from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StrategyGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""


class StrategyGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    members: list[str] | None = None


class StrategyGroupResponse(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    sort_order: int
    created_at: str | None = None
    updated_at: str | None = None
    members: list[dict] = Field(default_factory=list)


class StrategyListItem(BaseModel):
    strategy_id: str
    name: str
    description: str
    default_params: dict[str, Any]


class StrategySettingsResponse(BaseModel):
    strategy_id: str
    name: str
    enabled: bool
    params: dict[str, Any]
    default_params: dict[str, Any]
    updated_at: str | None = None


class StrategySettingsUpdate(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None


class MemberAdd(BaseModel):
    strategy_id: str


class MemberUpdate(BaseModel):
    sort_order: int | None = None


class ReorderRequest(BaseModel):
    members: list[str] = Field(..., min_length=1)
