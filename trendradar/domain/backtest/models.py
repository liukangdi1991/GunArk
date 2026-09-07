from __future__ import annotations

from dataclasses import dataclass, field
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
    # 顺延中的状态只用于展示，不参与"能不能卖"的判定
    blocked_since: Optional[date] = None
    blocked_reason: str = ""
    last_close: float = 0.0
    last_close_date: Optional[date] = None


@dataclass(frozen=True)
class OpenPositionRecord:
    """回测结束时仍卖不掉的持仓：钱还在它身上，但没有一笔成交可以对账。"""

    strategy: str
    code: str
    signal_date: date
    buy_date: date
    target_sell_date: date
    shares: int
    entry_price: float
    entry_cost: float
    mark_price: float
    mark_date: Optional[date]
    blocked_since: Optional[date]
    blocked_reason: str
    blocked_trading_days: int
    unrealized_pnl: float
    unrealized_return_pct: float


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
