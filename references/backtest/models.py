from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Signal:
    strategy: str
    code: str
    signal_date: date
    buy_date: date
    target_sell_date: date


@dataclass
class Position:
    strategy: str
    code: str
    signal_date: date
    entry_date: date
    target_sell_date: date
    entry_price: float
    shares: int
    entry_cost: float
    planned_sell_attempts: int = 0


@dataclass(frozen=True)
class TradeRecord:
    strategy: str
    code: str
    signal_date: date
    buy_date: date
    sell_date: date
    buy_price: float
    sell_price: float
    shares: int
    buy_amount: float
    sell_amount: float
    total_cost: float
    total_fee: float
    profit: float
    return_pct: float
    sell_postpone_days: int


@dataclass(frozen=True)
class SkipRecord:
    strategy: str
    code: str
    signal_date: date
    buy_date: Optional[date]
    stage: str
    reason: str
    date_ref: Optional[date] = None
