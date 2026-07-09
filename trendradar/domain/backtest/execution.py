from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from trendradar.domain.backtest.config import CostConfig


@dataclass(frozen=True)
class FillResult:
    price: float
    amount: float
    fee: float


def _limit_pct(*, code: str = "", is_st: bool = False) -> float:
    if is_st:
        return 0.05
    normalized = str(code or "").strip()
    if normalized.startswith(("300", "301", "688", "689")):
        return 0.20
    if normalized.startswith(("4", "8")):
        return 0.30
    return 0.10


def _round_tick(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_up_price(prev_close: float, *, code: str = "", is_st: bool = False) -> float:
    return _round_tick(float(prev_close) * (1 + _limit_pct(code=code, is_st=is_st)))


def limit_down_price(prev_close: float, *, code: str = "", is_st: bool = False) -> float:
    return _round_tick(float(prev_close) * (1 - _limit_pct(code=code, is_st=is_st)))


def is_limit_up(
    price: float, prev_close: float | None, *, code: str = "", eps: float = 1e-6, is_st: bool = False
) -> bool:
    if prev_close is None or float(prev_close) <= 0:
        return False
    return float(price) >= limit_up_price(float(prev_close), code=code, is_st=is_st) - eps


def is_limit_down(
    price: float, prev_close: float | None, *, code: str = "", eps: float = 1e-6, is_st: bool = False
) -> bool:
    if prev_close is None or float(prev_close) <= 0:
        return False
    return float(price) <= limit_down_price(float(prev_close), code=code, is_st=is_st) + eps


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
