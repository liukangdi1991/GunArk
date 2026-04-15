from __future__ import annotations

from dataclasses import dataclass

from backtest.config import CostConfig


@dataclass(frozen=True)
class FillResult:
    price: float
    amount: float
    fee: float


def is_limit_up(close: float, high: float, eps: float = 1e-6) -> bool:
    return abs(float(close) - float(high)) <= eps


def is_limit_down(close: float, low: float, eps: float = 1e-6) -> bool:
    return abs(float(close) - float(low)) <= eps


def calc_buy_fill(open_price: float, shares: int, costs: CostConfig) -> FillResult:
    fill_price = float(open_price) * (1 + costs.slippage_buy_bp / 10000.0)
    amount = fill_price * shares
    commission = max(costs.commission_min, amount * costs.commission_rate)
    transfer_fee = amount * costs.transfer_fee_rate
    fee = commission + transfer_fee
    return FillResult(price=fill_price, amount=amount, fee=fee)


def calc_sell_fill(close_price: float, shares: int, costs: CostConfig) -> FillResult:
    fill_price = float(close_price) * (1 - costs.slippage_sell_bp / 10000.0)
    amount = fill_price * shares
    commission = max(costs.commission_min, amount * costs.commission_rate)
    transfer_fee = amount * costs.transfer_fee_rate
    stamp_duty = amount * costs.stamp_duty_rate_sell
    fee = commission + transfer_fee + stamp_duty
    return FillResult(price=fill_price, amount=amount, fee=fee)
