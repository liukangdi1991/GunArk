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

    def get_rows(self, code: str, start_dt: date, end_dt: date) -> List[dict]:
        rows = self._data.get(code, {})
        return [
            r for d, r in sorted(rows.items())
            if start_dt <= d <= end_dt
        ]

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

        all_dates = [d0]
        current = _td(d0, 0)
        while current <= d_sell:
            if current not in all_dates:
                all_dates.append(current)
            current = _td(current, 1)
        all_dates = sorted(all_dates)

        rows = [
            {"date": _td(d0, -1), "open": 9.5, "close": 10.0, "high": 10.5, "low": 9.0, "volume": 1000000},
        ] + [
            {"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000}
            for d in all_dates
        ]
        for r in rows:
            if r["date"] == d1:
                r.update(open=10.1, close=10.5, high=11.0, low=10.0)
            if r["date"] == d_sell:
                r.update(open=11.0, close=12.0, high=12.5, low=10.8)

        store = FakeMarketStore({"000001": rows})

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

        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= _td(d0, 6):
            all_dates.append(cur)
            cur = _td(cur, 1)

        rows = [
            {"date": _td(d0, -1), "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
        ] + [
            {"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000}
            for d in all_dates
        ]
        for r in rows:
            if r["date"] == d1:
                r.update(open=11.0, close=11.0, high=11.0, low=11.0)

        store = FakeMarketStore({"000001": rows})

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

    def test_signal_day_close_limit_up_rejected(self):
        """entry_on_signal_day uses the close as the entry price, so a close
        limit-up (open not) must reject the buy — no lookahead open fills."""
        d0 = date(2026, 7, 1)

        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= _td(d0, 4):
            all_dates.append(cur)
            cur = _td(cur, 1)

        rows = [
            {"date": _td(d0, -1), "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
        ] + [
            {"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000}
            for d in all_dates
        ]
        # 信号日：开盘 9.5（未涨停），收盘 11.0（相对昨收 10.0 = +10% 涨停）
        for r in rows:
            if r["date"] == d0:
                r.update(open=9.5, close=11.0, high=11.0, low=9.5)

        store = FakeMarketStore({"000001": rows})
        signal_set = _make_signal_set("test", "Test", {d0: ["000001"]})
        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 1, "entry_on_signal_day": True},
        )
        result = BacktestEngine(config).run(signal_set, store)
        assert len(result.trades) == 0
        assert len(result.skips) == 1
        assert result.skips[0].reason == "涨停无法买入"

    def test_signal_day_close_entry_price_is_close(self):
        """entry_on_signal_day must fill at the signal-day close, never the open."""
        d0 = date(2026, 7, 1)

        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= _td(d0, 3):
            all_dates.append(cur)
            cur = _td(cur, 1)

        rows = [
            {"date": _td(d0, -1), "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
        ] + [
            {"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000}
            for d in all_dates
        ]
        # 信号日：open 9.5, close 10.5（+5%，未涨停）
        for r in rows:
            if r["date"] == d0:
                r.update(open=9.5, close=10.5, high=10.6, low=9.5)

        store = FakeMarketStore({"000001": rows})
        signal_set = _make_signal_set("test", "Test", {d0: ["000001"]})
        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 1, "entry_on_signal_day": True},
        )
        result = BacktestEngine(config).run(signal_set, store)
        assert len(result.trades) == 1
        assert result.trades[0].buy_date == d0
        assert result.trades[0].buy_price == pytest.approx(10.5 * 1.0002)  # close + 2bp 买入滑点

    def test_multiple_positions(self):
        d0 = date(2026, 7, 1)
        d1 = _td(d0, 1)
        d_sell = _td(d0, 6)

        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= d_sell:
            all_dates.append(cur)
            cur = _td(cur, 1)

        def rows_for(prev_open, prev_close, buy_open, buy_close, sell_open, sell_close):
            rows = [
                {"date": _td(d0, -1), "open": prev_open, "close": prev_close, "high": prev_close * 1.01, "low": prev_close * 0.99, "volume": 1000000},
            ] + [
                {"date": d, "open": prev_close, "close": prev_close, "high": prev_close * 1.01, "low": prev_close * 0.99, "volume": 1000000}
                for d in all_dates
            ]
            for r in rows:
                if r["date"] == d1:
                    r.update(open=buy_open, close=buy_close, high=buy_close * 1.02, low=buy_close * 0.98)
                if r["date"] == d_sell:
                    r.update(open=sell_open, close=sell_close, high=sell_close * 1.02, low=sell_close * 0.98)
            return rows

        store = FakeMarketStore(
            {
                "000001": rows_for(9.5, 10.0, 10.1, 10.5, 11.0, 12.0),
                "600519": rows_for(49.0, 50.0, 50.5, 52.0, 52.0, 55.0),
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

    def test_weekend_crossing_signals_use_trading_day_arithmetic(self):
        """Regression: buy/target dates must use trading-day indices, not calendar days.

        Signal on Thursday with hold=1: target lands on the following Monday
        (calendar Friday+2 would be Sunday). Previously this crashed with
        KeyError on the weekend target; Friday signals were also dropped
        because T+1 fell on Saturday.
        """
        # Trading calendar: Mon..Fri week, then next Mon/Tue (weekend skipped)
        calendar = [
            date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3),
            date(2026, 6, 4), date(2026, 6, 5),
            date(2026, 6, 8), date(2026, 6, 9),
        ]
        thu, fri, mon, tue = calendar[3], calendar[4], calendar[5], calendar[6]

        store = FakeMarketStore(
            {
                "000001": [
                    {"date": thu, "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
                    {"date": fri, "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
                    {"date": mon, "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
                    {"date": tue, "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1000000},
                ],
                "600519": [
                    {"date": thu, "open": 50.0, "close": 50.0, "high": 50.0, "low": 50.0, "volume": 500000},
                    {"date": fri, "open": 50.0, "close": 50.0, "high": 50.0, "low": 50.0, "volume": 500000},
                    {"date": mon, "open": 50.0, "close": 50.0, "high": 50.0, "low": 50.0, "volume": 500000},
                    {"date": tue, "open": 50.0, "close": 50.0, "high": 50.0, "low": 50.0, "volume": 500000},
                ],
            }
        )

        signal_set = _make_signal_set(
            "test", "Test",
            {thu: ["000001"], fri: ["600519"]},
        )

        config = _make_config(
            capital={"initial_cash": 200000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 1},
            portfolio={"max_daily_new_positions": 5, "max_positions": 5},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        # No crash; Thursday signal buys Friday, sells Monday; Friday signal
        # buys Monday, sells Tuesday.
        assert len(result.trades) == 2
        t0, t1 = result.trades
        assert (t0.code, t0.buy_date, t0.sell_date) == ("000001", fri, mon)
        assert (t1.code, t1.buy_date, t1.sell_date) == ("600519", mon, tue)
        for t in result.trades:
            assert t.sell_postpone_days == 0

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

    def test_realistic_cash_limits_positions(self):
        d0 = date(2026, 7, 1)
        d1 = _td(d0, 1)
        d_sell = _td(d0, 6)

        all_dates = [d0]
        cur = _td(d0, 1)
        while cur <= d_sell:
            all_dates.append(cur)
            cur = _td(cur, 1)

        def rows_for(prev_open, prev_close, buy_open, buy_close, sell_open, sell_close):
            rows = [
                {"date": _td(d0, -1), "open": prev_open, "close": prev_close, "high": prev_close * 1.01, "low": prev_close * 0.99, "volume": 1000000},
            ] + [
                {"date": d, "open": prev_close, "close": prev_close, "high": prev_close * 1.01, "low": prev_close * 0.99, "volume": 1000000}
                for d in all_dates
            ]
            for r in rows:
                if r["date"] == d1:
                    r.update(open=buy_open, close=buy_close, high=buy_close * 1.02, low=buy_close * 0.98)
                if r["date"] == d_sell:
                    r.update(open=sell_open, close=sell_close, high=sell_close * 1.02, low=sell_close * 0.98)
            return rows

        store = FakeMarketStore(
            {
                "000001": rows_for(9.5, 10.0, 50.0, 50.5, 52.0, 55.0),
                "600519": rows_for(49.0, 50.0, 50.5, 52.0, 52.0, 55.0),
            }
        )

        signal_set = _make_signal_set(
            "test", "Test",
            {d0: ["000001", "600519"]},
        )

        config = _make_config(
            capital={"initial_cash": 50000, "mode": "realistic", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
            portfolio={"max_daily_new_positions": 5, "max_positions": 5},
        )
        engine = BacktestEngine(config)

        result = engine.run(signal_set, store)
        assert len(result.trades) <= 1
        if result.trades:
            assert result.trades[0].total_cost <= 50000


class TestUltraShort:
    """超短线：信号日 T 收盘买入，T+1 收盘卖出（entry_on_signal_day + entry_at_close + hold=1）。"""

    def _config(self):
        return _make_config(execution={
            "entry_on_signal_day": True,
            "entry_at_close": True,
            "fixed_hold_n_days": 1,
        })

    def _rows(self):
        return {
            "000001": [
                {"date": date(2026, 7, 1), "open": 9.5, "close": 9.8, "high": 9.9, "low": 9.4},
                {"date": date(2026, 7, 2), "open": 9.9, "close": 10.0, "high": 10.1, "low": 9.8},
                {"date": date(2026, 7, 3), "open": 10.2, "close": 11.0, "high": 11.2, "low": 10.1},
            ],
        }

    def test_buys_signal_day_close_sells_next_close(self):
        engine = BacktestEngine(self._config())
        store = FakeMarketStore(self._rows())
        signal_set = _make_signal_set("t", "T", {date(2026, 7, 2): ["000001"]})
        result = engine.run(signal_set, store)
        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.buy_date == date(2026, 7, 2)       # T 日买入（不是 T+1）
        assert abs(t.buy_price - 10.0 * 1.0002) < 1e-6   # T 收盘价 + 2bp 买入滑点
        assert t.sell_date == date(2026, 7, 3)      # T+1 卖出
        assert abs(t.sell_price - 11.0 * 0.9998) < 1e-6  # T+1 收盘价 - 2bp 卖出滑点

    def test_open_limit_up_but_close_tradeable_buys(self):
        """收盘买入按收盘价判涨停：T 日开盘涨停但收盘回落（可买）→ 应买入。"""
        rows = self._rows()
        # T 日开盘涨停 10.78（前收 9.8 的 +10%），收盘 10.0 未涨停
        rows["000001"][1] = {"date": date(2026, 7, 2), "open": 10.78, "close": 10.0,
                             "high": 10.78, "low": 9.9}
        engine = BacktestEngine(self._config())
        store = FakeMarketStore(rows)
        signal_set = _make_signal_set("t", "T", {date(2026, 7, 2): ["000001"]})
        result = engine.run(signal_set, store)
        assert len(result.trades) == 1
        assert abs(result.trades[0].buy_price - 10.0 * 1.0002) < 1e-6

    def test_skips_close_limit_up(self):
        """收盘涨停（+10%）→ 收盘价买不进 → 放弃。"""
        rows = self._rows()
        # T 日收盘 10.78 = 前收 9.8 的 +10% 涨停
        rows["000001"][1] = {"date": date(2026, 7, 2), "open": 9.9, "close": 10.78,
                             "high": 10.78, "low": 9.8}
        engine = BacktestEngine(self._config())
        store = FakeMarketStore(rows)
        signal_set = _make_signal_set("t", "T", {date(2026, 7, 2): ["000001"]})
        result = engine.run(signal_set, store)
        assert result.trades == []
        assert any(s.stage == "buy" and s.reason == "涨停无法买入" for s in result.skips)

    def test_postpones_limit_down_sell(self):
        rows = self._rows()
        # T+1 收盘跌停 9.0（前收 10.0 的 -10%）→ 顺延到 T+2
        rows["000001"][2] = {"date": date(2026, 7, 3), "open": 9.0, "close": 9.0,
                             "high": 9.05, "low": 9.0}
        rows["000001"].append({"date": date(2026, 7, 6), "open": 9.2, "close": 9.5,
                               "high": 9.6, "low": 9.1})
        engine = BacktestEngine(self._config())
        store = FakeMarketStore(rows)
        signal_set = _make_signal_set("t", "T", {date(2026, 7, 2): ["000001"]})
        result = engine.run(signal_set, store)
        assert len(result.trades) == 1
        assert result.trades[0].sell_date == date(2026, 7, 6)
        assert result.trades[0].sell_postpone_days == 1

    def test_equity_curve_starts_at_first_buy_date(self):
        """equity 曲线从首个买入日开始（不再含信号前的平线段），且现金字段正确。"""
        dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(10)]
        rows = [
            {"date": d, "open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1000000}
            for d in dates
        ]
        store = FakeMarketStore({"000001": rows})
        signal_set = _make_signal_set("t", "T", {dates[2]: ["000001"]})
        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
        )
        result = BacktestEngine(config).run(signal_set, store)
        assert result.equity_curve[0]["date"] == dates[3]  # 常规模式 T+1 买入 → 首点 = 买入日
        assert result.metrics["initial_cash"] == 100000.0
        assert result.metrics["final_cash"] == result.equity_curve[-1]["equity"]
