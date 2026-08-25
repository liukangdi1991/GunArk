from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class CapitalConfig:
    initial_cash: float = 1_000_000.0
    lot_size: int = 100
    position_budget_cash: float = 100_000.0
    max_cash_usage_pct: Optional[float] = None
    mode: Literal["realistic", "unlimited_cash"] = "unlimited_cash"
    fixed_cash_per_trade: float = 50_000.0


@dataclass(frozen=True)
class ExecutionConfig:
    fixed_hold_n_days: int = 5
    max_sell_postpone_days: int = 10
    reject_if_limit_up_on_buy: bool = True
    postpone_if_limit_down_on_sell: bool = True
    force_sell_on_two_day_close_below_long_term_bull_bear_line: bool = False
    close_below_recent_low_stop_window: Optional[int] = None
    entry_on_signal_day: bool = False   # 信号日当日入场（默认 T+1 入场）——超短线
    entry_at_close: bool = False        # 入场价用收盘价（默认开盘价）——超短线


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_duty_rate_sell: float = 0.0005  # 现行 A 股卖出印花税 0.05%（2023-08 起）
    transfer_fee_rate: float = 0.00001
    slippage_buy_bp: float = 2.0
    slippage_sell_bp: float = 2.0


@dataclass(frozen=True)
class PortfolioConfig:
    target_positions: Optional[int] = None
    max_positions: Optional[int] = None
    max_single_position_pct: Optional[float] = None
    max_daily_new_positions: Optional[int] = None
    allow_reentry_same_stock: bool = False


@dataclass(frozen=True)
class RiskConfig:
    benchmark: str = "000300.SH"
    trading_days_per_year: int = 252
    risk_free_rate: float = 0.02


@dataclass(frozen=True)
class BacktestConfig:
    capital: CapitalConfig = CapitalConfig()
    execution: ExecutionConfig = ExecutionConfig()
    costs: CostConfig = CostConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    risk: RiskConfig = RiskConfig()
