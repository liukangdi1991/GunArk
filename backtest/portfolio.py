from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from backtest.config import BacktestConfig
from backtest.models import Position


@dataclass
class PortfolioState:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    closed_codes: set[str] = field(default_factory=set)


def calc_position_budget(config: BacktestConfig, equity: float, cash: float) -> float:
    caps: List[float] = []
    if config.portfolio.target_positions:
        caps.append(equity / config.portfolio.target_positions)
    if config.portfolio.max_single_position_pct is not None:
        caps.append(equity * config.portfolio.max_single_position_pct)
    if config.capital.max_cash_usage_pct is not None:
        caps.append(cash * config.capital.max_cash_usage_pct)

    if caps:
        return max(0.0, min(caps))
    return max(float(config.capital.lot_size), float(config.capital.position_budget_cash))


def open_position(state: PortfolioState, position: Position, cash_used: float) -> None:
    state.positions[position.code] = position
    state.cash -= cash_used


def close_position(state: PortfolioState, code: str, cash_back: float, allow_reentry: bool) -> Position:
    pos = state.positions.pop(code)
    state.cash += cash_back
    if not allow_reentry:
        state.closed_codes.add(code)
    return pos


def current_equity(state: PortfolioState, mark_prices: Dict[str, float]) -> float:
    market_value = sum(mark_prices.get(code, pos.entry_price) * pos.shares for code, pos in state.positions.items())
    return state.cash + market_value


def available_open_slots(state: PortfolioState, max_positions: int | None) -> int:
    if max_positions is None:
        return 10**9
    return max(0, max_positions - len(state.positions))


def filter_reentry_codes(state: PortfolioState, codes: List[str], allow_reentry: bool) -> List[str]:
    if allow_reentry:
        return codes
    return [code for code in codes if code not in state.closed_codes]


def is_holding(state: PortfolioState, code: str) -> bool:
    return code in state.positions
