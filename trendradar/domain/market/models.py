"""Market data domain models."""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Bar:
    """Single OHLCV bar for a stock on a trading day."""
    code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    adj_factor: float = 1.0
    is_suspended: bool = False


@dataclass(frozen=True)
class StockMeta:
    """Metadata for a single stock."""
    code: str
    name: str = ""
    industry: str = ""
    market: str = ""
    list_date: Optional[date] = None
