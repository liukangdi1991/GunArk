from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from trendradar.domain.backtest.models import Position

# 持仓键 = (code, strategy)：一个策略一只股票同时只持一份，不同策略互不干扰。
PositionKey = Tuple[str, str]


@dataclass
class PortfolioState:
    cash: float
    positions: Dict[PositionKey, Position] = field(default_factory=dict)
    closed_keys: set[PositionKey] = field(default_factory=set)

    def open_position(self, code: str, strategy: str, position: Position, cash_used: float) -> None:
        self.positions[(code, strategy)] = position
        self.cash -= cash_used

    def close_position(self, code: str, strategy: str, cash_back: float) -> Position:
        pos = self.positions.pop((code, strategy))
        self.cash += cash_back
        self.closed_keys.add((code, strategy))
        return pos

    def available_open_slots(self, max_positions: int | None) -> int:
        if max_positions is None:
            return 10 ** 9
        return max(0, max_positions - len(self.positions))

    def is_holding(self, code: str, strategy: str) -> bool:
        return (code, strategy) in self.positions


def available_open_slots(state: PortfolioState, max_positions: int | None) -> int:
    return state.available_open_slots(max_positions)


def is_holding(state: PortfolioState, code: str, strategy: str) -> bool:
    return state.is_holding(code, strategy)


def filter_reentry_keys(
    state: PortfolioState, keys: List[PositionKey], allow_reentry: bool
) -> List[PositionKey]:
    if allow_reentry:
        return keys
    return [k for k in keys if k not in state.closed_keys]


def current_equity(state: PortfolioState, mark_prices: Dict[PositionKey, float]) -> float:
    market_value = sum(
        mark_prices.get((pos.code, pos.strategy), pos.entry_price) * pos.shares
        for pos in state.positions.values()
    )
    return state.cash + market_value
