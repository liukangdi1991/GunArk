from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

import pytest

from trendradar.domain.backtest.config import BacktestConfig
from trendradar.domain.backtest.engine import BacktestEngine
from trendradar.domain.backtest.models import Signal, TradeRecord
from trendradar.domain.signal.models import SignalSet, StrategySignal


class FakeMarketStore:
    def __init__(self, rows: Dict[str, List[dict]]) -> None:
        self._data: Dict[str, Dict[date, dict]] = {}
        for code, row_list in rows.items():
            self._data[code] = {}
            for r in row_list:
                self._data[code][r["date"]] = r

    def get_row(self, code: str, dt: date) -> Optional[dict]:
        return self._data.get(code, {}).get(dt)

    def get_previous_close(self, code: str, dt: date) -> Optional[float]:
        rows = self._data.get(code, {})
        dates = sorted(d for d in rows if d < dt)
        if not dates:
            return None
        return float(rows[dates[-1]]["close"])

    def get_calendar(self) -> List[date]:
        all_dates = set()
        for rows in self._data.values():
            all_dates.update(rows.keys())
        return sorted(all_dates)


def _td(d: date, n: int) -> date:
    return d + timedelta(days=n)


def _make_signal_set(
    strategy_id: str,
    strategy_name: str,
    codes_by_date: Dict[date, List[str]],
) -> SignalSet:
    signals = []
    for d, codes in codes_by_date.items():
        signals.append(
            StrategySignal(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                signal_date=d,
                codes=codes,
            )
        )
    return SignalSet(signals=signals)


def _make_config(**kwargs) -> BacktestConfig:
    from trendradar.domain.backtest.config import (
        CapitalConfig,
        CostConfig,
        ExecutionConfig,
        PortfolioConfig,
    )

    return BacktestConfig(
        capital=CapitalConfig(**kwargs.pop("capital", {})),
        execution=ExecutionConfig(**kwargs.pop("execution", {})),
        costs=CostConfig(**kwargs.pop("costs", {})),
        portfolio=PortfolioConfig(**kwargs.pop("portfolio", {})),
    )


class TestBacktestEngine:
    def test_run_with_no_signals(self):
        config = _make_config()
        engine = BacktestEngine(config)
        signal_set = SignalSet()
        store = FakeMarketStore({})

        result = engine.run(signal_set, store)
        assert result.trades == []
        assert result.skips == []
        assert result.equity_curve == []

    def test_run_with_empty_signal_codes(self):
        config = _make_config()
        engine = BacktestEngine(config)
        signal_set = _make_signal_set(
            "test", "Test",
            {date(2026, 7, 1): []},
        )
        store = FakeMarketStore({})

        result = engine.run(signal_set, store)
        assert result.trades == []

    def test_simple_buy_hold_sell(self):
        d0 = date(2026, 7, 1)
        d1 = _td(d0, 1)
        d_sell = _td(d0, 6)  # hold 5 days, sell on 6th

        all_dates = [d0, d1]
        current = _td(d0, 0)
        while current <= d_sell:
            if current not in all_dates:
                all_dates.append(current)
            current = _td(current, 1)
        all_dates = sorted(all_dates)

        store = FakeMarketStore(
            {
                "000001": [
                    {"date": _td(d0, -1), "open": 9.5, "close": 10.0, "high": 10.5, "low": 9.0, "volume": 1000000},
                    {"date": d1, "open": 10.1, "close": 10.5, "high": 11.0, "low": 10.0, "volume": 2000000},
                    {"date": d_sell, "open": 11.0, "close": 12.0, "high": 12.5, "low": 10.8, "volume": 3000000},
                ]
            }
        )

        signal_set = _make_signal_set(
            "test", "Test",
            {d0: ["000001"]},
        )

        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        assert len(result.trades) == 1
        assert result.trades[0].code == "000001"
        assert result.trades[0].buy_date == d1
        assert result.trades[0].sell_date == d_sell
        assert len(result.equity_curve) > 0

    def test_limit_up_rejection(self):
        d0 = date(2026, 7, 1)
        d1 = _td(d0, 1)

        store = FakeMarketStore(
            {
                "000001": [
                    {"date": _td(d0, -1), "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
                    {"date": d1, "open": 11.0, "close": 11.0, "high": 11.0, "low": 11.0, "volume": 2000000},
                ]
            }
        )

        signal_set = _make_signal_set(
            "test", "Test",
            {d0: ["000001"]},
        )

        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5, "reject_if_limit_up_on_buy": True},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        assert len(result.trades) == 0
        assert len(result.skips) == 1
        assert result.skips[0].reason == "涨停无法买入"

    def test_multiple_positions(self):
        d0 = date(2026, 7, 1)
        d1 = _td(d0, 1)
        d_sell = _td(d0, 6)

        store = FakeMarketStore(
            {
                "000001": [
                    {"date": _td(d0, -1), "open": 9.5, "close": 10.0, "high": 10.5, "low": 9.0, "volume": 1000000},
                    {"date": d1, "open": 10.1, "close": 10.5, "high": 11.0, "low": 10.0, "volume": 2000000},
                    {"date": d_sell, "open": 11.0, "close": 12.0, "high": 12.5, "low": 10.8, "volume": 3000000},
                ],
                "600519": [
                    {"date": _td(d0, -1), "open": 49.0, "close": 50.0, "high": 51.0, "low": 48.0, "volume": 500000},
                    {"date": d1, "open": 50.5, "close": 52.0, "high": 53.0, "low": 50.0, "volume": 600000},
                    {"date": d_sell, "open": 52.0, "close": 55.0, "high": 56.0, "low": 51.0, "volume": 700000},
                ],
            }
        )

        signal_set = _make_signal_set(
            "test", "Test",
            {d0: ["000001", "600519"]},
        )

        config = _make_config(
            capital={"initial_cash": 200000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
            portfolio={"max_daily_new_positions": 2, "max_positions": 5},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        assert len(result.trades) == 2
        codes = {t.code for t in result.trades}
        assert codes == {"000001", "600519"}

    def test_equity_curve_shape(self):
        d0 = date(2026, 7, 1)
        d_sell = _td(d0, 6)
        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= d_sell:
            all_dates.append(cur)
            cur = _td(cur, 1)

        rows = [{"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000} for d in all_dates]
        rows[0] = {"date": all_dates[0], "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000}

        d1 = all_dates[1]
        if d1 in [r["date"] for r in rows]:
            for i, r in enumerate(rows):
                if r["date"] == d1:
                    rows[i] = {**r, "open": 10.1, "close": 10.5, "high": 11.0, "low": 10.0}
                    break

        ds = all_dates[-1]
        for i, r in enumerate(rows):
            if r["date"] == ds:
                rows[i] = {**r, "open": 11.0, "close": 12.0, "high": 12.5, "low": 10.8}
                break

        store = FakeMarketStore({"000001": rows})

        signal_set = _make_signal_set(
            "test", "Test",
            {all_dates[0]: ["000001"]},
        )

        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        assert len(result.equity_curve) > 0
        for row in result.equity_curve:
            assert "date" in row
            assert "equity" in row
            assert "cash" in row
            assert "position_count" in row
