"""A-share daily backtest package."""

from .config import BacktestConfig, default_config
from .engine import BacktestEngine

__all__ = ["BacktestConfig", "BacktestEngine", "default_config"]
