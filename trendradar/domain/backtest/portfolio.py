from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from trendradar.domain.backtest.models import Position


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    closed_codes: set[str] = field(default_factory=set)

    def open_position(self, code: str, position: Position, cash_used: float) -> None:
        self.positions[code] = position
        self.cash -= cash_used

    def close_position(self, code: str, cash_back: float) -> Position:
        pos = self.positions.pop(code)
        self.cash += cash_back
        self.closed_codes.add(code)
        return pos

    def available_open_slots(self, max_positions: int | None) -> int:
        if max_positions is None:
            return 10 ** 9
        return max(0, max_positions - len(self.positions))

    def is_holding(self, code: str) -> bool:
        return code in self.positions


def available_open_slots(state: PortfolioState, max_positions: int | None) -> int:
    return state.available_open_slots(max_positions)


def is_holding(state: PortfolioState, code: str) -> bool:
    return state.is_holding(code)


def filter_reentry_codes(state: PortfolioState, codes: List[str], allow_reentry: bool) -> List[str]:
    if allow_reentry:
        return codes
    return [code for code in codes if code not in state.closed_codes]


def current_equity(state: PortfolioState, mark_prices: Dict[str, float]) -> float:
    market_value = sum(
        mark_prices.get(code, pos.entry_price) * pos.shares
        for code, pos in state.positions.items()
    )
    return state.cash + market_value
