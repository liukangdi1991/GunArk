from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CapitalConfig:
    initial_cash: float = 1_000_000.0
    lot_size: int = 100
    # 为每个新开仓位提供的名义资金（当仓位限制关闭时使用）
    position_budget_cash: float = 100_000.0
    # 不填表示不做现金约束；填 0~1 表示最多可使用现金比例
    max_cash_usage_pct: Optional[float] = None


@dataclass(frozen=True)
class ExecutionConfig:
    buy_delay_days: int = 1
    sell_delay_days: int = 8
    max_sell_postpone_days: int = 10
    reject_if_limit_up_on_buy: bool = True
    postpone_if_limit_down_on_sell: bool = True
    skip_if_suspended: bool = True


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_duty_rate_sell: float = 0.0001
    transfer_fee_rate: float = 0.00001
    slippage_buy_bp: float = 2.0
    slippage_sell_bp: float = 2.0


@dataclass(frozen=True)
class PortfolioConfig:
    # 不填表示不限制
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
class PathConfig:
    signal_dir: str = "results/polars"
    parquet_dir: str = "db"
    csv_dir: str = "data"
    output_root: str = "backtest_results"


@dataclass(frozen=True)
class BacktestConfig:
    capital: CapitalConfig = CapitalConfig()
    execution: ExecutionConfig = ExecutionConfig()
    costs: CostConfig = CostConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    risk: RiskConfig = RiskConfig()
    paths: PathConfig = PathConfig()


def default_config() -> BacktestConfig:
    return BacktestConfig()
