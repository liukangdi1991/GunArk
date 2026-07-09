from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    description: str
    selector_class: type  # implements SelectionStrategy
    default_params: dict[str, Any] = field(default_factory=dict)
    param_schema: dict[str, type] = field(default_factory=dict)


@dataclass
class StrategyGroup:
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class StrategyGroupMember:
    group_id: str
    strategy_id: str
    sort_order: int = 0


@dataclass
class StrategySettings:
    strategy_id: str
    enabled: bool = True
    params_json: str = "{}"
