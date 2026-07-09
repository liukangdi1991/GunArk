from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol
import polars as pl


@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    market_data: pl.DataFrame
    candidate_codes: list[str]
    get_data_dict: Callable[[], dict[str, pl.DataFrame]]


@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float = 0.0


class SelectionStrategy(Protocol):
    definition: "StrategyDefinition"

    def select(self, context: SelectionContext) -> SelectionResult: ...
