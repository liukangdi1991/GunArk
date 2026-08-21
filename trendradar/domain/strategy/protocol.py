"""Selection strategy protocol and context types.

V2 performance model: warmup once per selection, select_day per trading day.
Indicators are computed once in warmup; per-candidate history is accessed
through the grouped WarmupResult (partition_by code) instead of full-table
filters. Selectors are stateless beyond self.definition; warmup results are
passed explicitly, never stored on the selector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

import polars as pl


@dataclass(frozen=True)
class WarmupResult:
    """Precomputed per-code data (indicator columns included), grouped by code.

    group[code] is that stock's full history, ordered by [code, date], with
    indicator columns attached. Must be read-only during select_day.
    """
    grouped: dict[str, pl.DataFrame]


@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    candidate_codes: list[str] | None = None   # None = full market (runner always passes a list today)
    market_data: pl.DataFrame = field(default_factory=pl.DataFrame)


@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float


class SelectionStrategy(Protocol):
    definition: "StrategyDefinition"

    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        """Precompute indicator columns and group by code (called once)."""
        ...

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        """Judge one trading day from precomputed data (called per day)."""
        ...
