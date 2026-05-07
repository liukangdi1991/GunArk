from __future__ import annotations

from datetime import date

import pandas as pd

from backtest import BacktestEngine, default_config
from backtest.models import Position
from backtest.service import build_config


class _DummyMarket:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def load_code(self, code: str) -> pd.DataFrame:
        return self._df.copy()


def test_ten_day_low_stop_strategy_disables_long_term_bull_bear_stop() -> None:
    cfg = build_config(trade_strategy="ten_day_low_stop")

    assert cfg.execution.force_sell_on_two_day_close_below_long_term_bull_bear_line is False
    assert cfg.execution.close_below_recent_low_stop_window == 10


def test_recent_low_stop_triggers_only_when_today_close_breaks_prior_holding_intraday_low() -> None:
    cfg = build_config(trade_strategy="ten_day_low_stop")
    engine = BacktestEngine(cfg)
    engine.market = _DummyMarket(
        pd.DataFrame(
            {
                "date": [
                    date(2026, 4, 1),
                    date(2026, 4, 2),
                    date(2026, 4, 3),
                    date(2026, 4, 6),
                    date(2026, 4, 7),
                    date(2026, 4, 8),
                ],
                "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
                "close": [10.8, 10.6, 10.4, 10.2, 10.1, 9.7],
                "high": [11.0, 11.0, 11.0, 11.0, 11.0, 11.0],
                "low": [10.7, 10.3, 10.2, 9.8, 9.9, 9.6],
                "volume": [100, 100, 100, 100, 100, 100],
            }
        )
    )
    position = Position(
        strategy="测试策略",
        code="000001",
        signal_date=date(2026, 4, 1),
        entry_date=date(2026, 4, 2),
        target_sell_date=date(2026, 4, 20),
        entry_price=10.0,
        shares=100,
        entry_cost=1000.0,
    )

    assert engine._is_close_below_recent_low_stop("000001", date(2026, 4, 8), position) is True

    engine.market = _DummyMarket(
        pd.DataFrame(
            {
                "date": [date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6)],
                "open": [10.0, 10.0, 10.0],
                "close": [10.5, 10.1, 10.0],
                "high": [11.0, 11.0, 11.0],
                "low": [10.4, 9.8, 10.0],
                "volume": [100, 100, 100],
            }
        )
    )

    assert engine._is_close_below_recent_low_stop("000001", date(2026, 4, 6), position) is False
