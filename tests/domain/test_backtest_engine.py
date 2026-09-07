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

    def test_no_trades_reports_capital_unchanged(self):
        """空曲线不等于钱亏了：否则报告会出现"初始 100 万 → 最终 0"却写着 0% 收益。

        只有 realistic 模式有"账户"可言——unlimited_cash 不产净值曲线（见
        2026-09-04-unlimited-cash-money-only-metrics-design.md）。
        """
        config = _make_config(capital={"initial_cash": 100000, "mode": "realistic"})
        result = BacktestEngine(config).run(SignalSet(), FakeMarketStore({}))

        assert result.equity_curve == []
        assert result.metrics["initial_cash"] == 100000.0
        assert result.metrics["final_cash"] == 100000.0

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
        assert result.trades[0].buy_amount > 0

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
        """曲线的字段形状是 realistic 模式的契约——unlimited 模式不产曲线。"""
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
            capital={"initial_cash": 100000, "mode": "realistic", "position_budget_cash": 50000},
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
            capital={"initial_cash": 100000, "mode": "realistic", "position_budget_cash": 50000},
        )
        result = BacktestEngine(config).run(signal_set, store)
        assert result.equity_curve[0]["date"] == dates[3]  # 常规模式 T+1 买入 → 首点 = 买入日
        assert result.metrics["initial_cash"] == 100000.0
        assert result.metrics["final_cash"] == result.equity_curve[-1]["equity"]


def _down_chain(start_px: float, n: int) -> list[float]:
    """连续跌停价链：每根 = 前收 × 0.9（主板 10% 档）。"""
    px, out, prev = start_px, [], start_px
    for _ in range(n):
        px = round(prev * 0.9, 2)
        out.append(px)
        prev = px
    return out


class TestSellPostponesIndefinitely:
    """卖不掉就一直等：跌停封死 / 停牌无行情都不强行成交，等到的那天按当日收盘卖。

    旧实现在顺延 N 天后按跌停收盘价强行成交——而"跌停卖不掉"正是顺延的前提，
    等于自己否掉自己；跨 episode 累积的 attempts 还会让后一次信号一次宽限都拿不到。
    """

    def _cfg(self, hold=2, mode="unlimited_cash"):
        return _make_config(
            capital={"initial_cash": 100000, "mode": mode,
                     "fixed_cash_per_trade": 50000, "position_budget_cash": 100000},
            execution={"fixed_hold_n_days": hold},
        )

    def test_postpone_cap_and_counter_are_gone(self):
        """上限与计数器整体删除：没有"顺延到第 N 天就不管能不能成交都平仓"这回事了。"""
        from trendradar.domain.backtest.config import ExecutionConfig
        from trendradar.domain.backtest.models import Position

        assert not hasattr(ExecutionConfig(), "max_sell_postpone_days")
        assert "planned_sell_attempts" not in Position.__dataclass_fields__

    def test_long_limit_down_streak_sells_on_first_tradeable_day(self):
        """12 个连续跌停日（旧上限 10 会在第 11 天强行成交）→ 必须等到打开跌停那天。"""
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(20)]
        chain = _down_chain(10.0, 12)
        rows = [{"date": d[0], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[1], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9}]
        for i, px in enumerate(chain):
            rows.append({"date": d[2 + i], "open": px, "close": px,
                         "high": px * 1.01, "low": px})
        open_px = round(chain[-1] * 1.05, 2)      # 打开跌停：+5%，非跌停
        last = d[2 + len(chain)]
        rows.append({"date": last, "open": open_px, "close": open_px,
                     "high": open_px, "low": chain[-1]})

        store = FakeMarketStore({"000001": rows})
        result = BacktestEngine(self._cfg()).run(
            _make_signal_set("t", "T", {d[0]: ["000001"]}), store)

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.sell_date == last
        assert abs(t.sell_price - open_px * 0.9998) < 1e-6
        assert [s.reason for s in result.skips] == []   # 没有任何"强制平仓"

    def test_still_blocked_at_end_is_reported_as_open_position(self):
        """跌停到日历末尾 → 不产生成交，但必须出现在 open_positions 并说明为什么。"""
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(20)]
        chain = _down_chain(10.0, 6)
        rows = [{"date": d[0], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[1], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9}]
        for i, px in enumerate(chain):
            rows.append({"date": d[2 + i], "open": px, "close": px,
                         "high": px * 1.01, "low": px})

        store = FakeMarketStore({"000001": rows})
        result = BacktestEngine(self._cfg()).run(
            _make_signal_set("t", "T", {d[0]: ["000001"]}), store)

        assert result.trades == []
        assert len(result.open_positions) == 1
        op = result.open_positions[0]
        assert op.code == "000001" and op.strategy == "t"
        assert op.blocked_reason == "跌停封死"
        assert op.blocked_since == d[3]            # 目标卖出日 = buy(d1)+2 = d3
        assert op.blocked_trading_days == 5        # d3..d7，跌停链的后 5 天
        assert abs(op.mark_price - chain[-1]) < 1e-6   # 按最后一根真实收盘价标记，不是成本
        assert op.mark_date == d[2 + 5]
        assert op.unrealized_pnl < 0 and op.unrealized_return_pct < 0

    def test_suspended_at_end_reports_no_quote_reason(self):
        """停牌到末尾（目标卖出日无行情）→ 原因写"停牌无行情"，标记价用停牌前收盘。"""
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(12)]
        other = [{"date": x, "open": 5.0, "close": 5.0, "high": 5.1, "low": 4.9} for x in d]
        rows = [{"date": d[0], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[1], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[2], "open": 18.0, "close": 20.0, "high": 20.0, "low": 17.9}]

        store = FakeMarketStore({"000001": rows, "000002": other})
        result = BacktestEngine(self._cfg()).run(
            _make_signal_set("t", "T", {d[0]: ["000001"]}), store)

        assert result.trades == []
        op = result.open_positions[0]
        assert op.blocked_reason == "停牌无行情"
        assert op.blocked_since == d[3]
        assert abs(op.mark_price - 20.0) < 1e-6
        assert op.mark_date == d[2]

    def test_suspension_does_not_mark_position_at_cost(self):
        """停牌期间净值不能被"回落到买入成本"抹掉浮盈：这是条凭空回撤。

        净值曲线只在 realistic 模式下存在，所以这条也得用它。
        """
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(12)]
        other = [{"date": x, "open": 5.0, "close": 5.0, "high": 5.1, "low": 4.9} for x in d]
        rows = [{"date": d[0], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[1], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[2], "open": 18.0, "close": 20.0, "high": 20.0, "low": 17.9}]

        store = FakeMarketStore({"000001": rows, "000002": other})
        result = BacktestEngine(self._cfg(mode="realistic")).run(
            _make_signal_set("t", "T", {d[0]: ["000001"]}), store)

        marked = [p for p in result.equity_curve if p["date"] >= d[3]]
        assert marked and all(p["position_count"] == 1 for p in marked)
        assert abs(marked[-1]["equity"] - marked[0]["equity"]) < 1e-6   # 停牌期净值持平
        assert marked[-1]["equity"] > result.metrics["initial_cash"]    # 浮盈没被抹掉


class TestUnlimitedCashIsMoneyOnly:
    """unlimited_cash 是"每个信号投一份名义额"的采样器，不是账户：不产净值曲线。

    全市场实跑（20260903_105335_backtest_de887fb4）：首日现金 −2.33 亿、持仓 4,816 只、
    期末权益 −550 万，`total_return_pct` 却报 **−1302.62%**——分母取的是曲线首点
    （当日买入后的市值 45.8 万，不是 100 万本金），净值穿越 0 之后 maxDD（−2190.52%）
    与 sharpe 一并失去定义，annual 被夹成 −100。同一份数据里该看的那个数是
    Σ盈亏 / Σ投入 = −647.9 万 / 2.45 亿 = **−2.65%**。
    口径见 docs/superpowers/specs/2026-09-04-unlimited-cash-money-only-metrics-design.md。
    """

    NAV_KEYS = ("total_return_pct", "annual_return_pct", "max_drawdown_pct",
                "sharpe", "initial_cash", "final_cash")

    @staticmethod
    def _rows(days, close_on_sell):
        out = []
        for i, d in enumerate(days):
            px = close_on_sell if i == len(days) - 4 else 10.0
            out.append({"date": d, "open": 10.0, "close": px,
                        "high": max(px, 10.0), "low": min(px, 10.0)})
        return out

    def _fixture(self, store_cls=FakeMarketStore):
        """两只票各投 5 万，第 6 个交易日 12 块卖出：本金 1000 元，现金必然为负。"""
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(10)]
        rows = {c: self._rows(d, 12.0) for c in ("000001", "000002")}
        store = store_cls(rows)
        signal_set = _make_signal_set("t", "T", {d[0]: ["000001", "000002"]})
        config = _make_config(
            capital={"initial_cash": 1000.0, "mode": "unlimited_cash",
                     "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        return store, signal_set, config, d

    def test_no_equity_curve_and_no_nav_metrics(self):
        store, signal_set, config, _ = self._fixture()
        result = BacktestEngine(config).run(signal_set, store)

        assert len(result.trades) == 2
        assert result.equity_curve == []
        for key in self.NAV_KEYS:
            assert result.metrics[key] is None, key

    def test_money_metrics_replace_nav_metrics(self):
        store, signal_set, config, _ = self._fixture()
        result = BacktestEngine(config).run(signal_set, store)
        m = result.metrics

        realized = sum(t.profit for t in result.trades)
        notional = sum(t.buy_amount for t in result.trades)
        assert m["realized_profit_sum"] == pytest.approx(realized)
        assert m["invested_notional_sum"] == pytest.approx(notional)
        assert m["pnl_return_pct"] == pytest.approx(realized / notional * 100.0)
        assert m["unrealized_pnl"] == pytest.approx(0.0)
        assert m["trade_count"] == 2
        assert m["win_rate_pct"] == pytest.approx(100.0)
        # 逐笔口径是个"正常的数"，不是 −1302% 那种塌缩产物
        assert 0 < m["pnl_return_pct"] < 100

    def test_held_position_is_not_reread_once_per_day_for_a_mark(self):
        """日终标记只为画曲线而存在：曲线没了，就不该每天有这笔读。

        持有期内的正常用量是每天一次"该不该卖"的判断（unlimited 模式入场不读权益）。
        """
        from collections import Counter

        store, signal_set, config, days = self._fixture(store_cls=_CountingStore)
        BacktestEngine(config).run(signal_set, store)

        sell_day = days[-4]
        per_key = Counter(store.reads)
        holding = {k: v for k, v in per_key.items() if k[1] < sell_day}
        assert holding, "没跑到持有期，测试无效"
        assert max(holding.values()) <= 1, Counter(store.reads).most_common(3)

    def test_open_position_is_still_marked_once_at_the_end(self):
        """不日标 ≠ 不标：期末未平仓的标记价/浮动盈亏仍要反映最后一根真实收盘价。"""
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(20)]
        chain = _down_chain(10.0, 6)
        rows = [{"date": d[0], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
                {"date": d[1], "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9}]
        for i, px in enumerate(chain):
            rows.append({"date": d[2 + i], "open": px, "close": px,
                         "high": px * 1.01, "low": px})
        store = FakeMarketStore({"000001": rows})
        config = _make_config(
            capital={"initial_cash": 100000, "mode": "unlimited_cash",
                     "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 2},
        )
        result = BacktestEngine(config).run(
            _make_signal_set("t", "T", {d[0]: ["000001"]}), store)

        assert result.trades == []
        op = result.open_positions[0]
        assert abs(op.mark_price - chain[-1]) < 1e-6
        assert op.mark_date == d[2 + len(chain) - 1]
        assert result.metrics["unrealized_pnl"] == pytest.approx(op.unrealized_pnl)
        assert result.metrics["realized_profit_sum"] == pytest.approx(0.0)
        assert result.metrics["pnl_return_pct"] is None   # 一分钱投入都没有

    def test_realistic_mode_keeps_nav_metrics(self):
        d = [date(2026, 7, 1) + timedelta(days=i) for i in range(10)]
        rows = {c: self._rows(d, 12.0) for c in ("000001", "000002")}
        config = _make_config(
            capital={"initial_cash": 1_000_000.0, "mode": "realistic",
                     "position_budget_cash": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        result = BacktestEngine(config).run(
            _make_signal_set("t", "T", {d[0]: ["000001", "000002"]}),
            FakeMarketStore(rows))

        assert result.equity_curve
        for key in self.NAV_KEYS:
            assert isinstance(result.metrics[key], float), key
        # 逐笔金额口径两种模式都给，报告页两张表都有的看
        assert result.metrics["invested_notional_sum"] > 0


class _CountingStore(FakeMarketStore):
    """记录每次 get_row 的 (code, date)。"""

    def __init__(self, rows):
        super().__init__(rows)
        self.reads: list[tuple[str, date]] = []

    def get_row(self, code, dt):
        self.reads.append((code, dt))
        return super().get_row(code, dt)


def _realistic_reads(n_candidates: int = 30):
    d = [date(2026, 7, 1) + timedelta(days=i) for i in range(24)]
    rows = {
        f"{i:06d}": [{"date": x, "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9}
                     for x in d]
        for i in range(60)
    }
    cfg = _make_config(
        capital={"mode": "realistic", "initial_cash": 10_000_000_000,
                 "position_budget_cash": 100_000},
        execution={"fixed_hold_n_days": 5},
    )
    store = _CountingStore(rows)
    BacktestEngine(cfg).run(
        _make_signal_set("t", "T", {d[0]: [f"{i:06d}" for i in range(10)],
                                    d[1]: [f"{10+i:06d}" for i in range(n_candidates)]}),
        store,
    )
    return store.reads


def test_same_stock_is_not_reread_once_per_candidate_on_the_same_day():
    """同一天同一只票最多读几眼，不能被候选数放大。

    realistic 模式的预算原来在每个候选入场前都把全部持仓重新标记一遍，而标记
    要整文件读 parquet ⇒ 成本是 候选数 × 持仓数。全市场实测 92% 的 get_row
    来自这里（10.8 万次，占 343 秒里的 281 秒）。
    """
    from collections import Counter

    per_key = Counter(_realistic_reads())
    # 一天里一只票的正常用量：入场判一次 + 当日净值标记一次 + 卖出判断一次
    assert max(per_key.values()) <= 3, per_key.most_common(3)


class TestEntryDedupKeyIsCodeStrategy:
    """入场去重键是 (code, strategy)：不同策略可同日同票各自建仓，同策略持仓期间不重复买。"""

    def _rows(self, dates: list[date], price: float = 10.0) -> dict:
        return {
            "000001": [
                {"date": d, "open": price, "close": price, "high": price * 1.01, "low": price * 0.99}
                for d in dates
            ]
        }

    def test_two_strategies_same_code_same_day_both_trade(self):
        dates = [date(2026, 7, i) for i in range(1, 15) if date(2026, 7, i).weekday() < 5]
        store = FakeMarketStore(self._rows(dates))
        signal_set = SignalSet(
            signals=[
                StrategySignal(strategy_id="stratA", strategy_name="A", signal_date=dates[0], codes=["000001"]),
                StrategySignal(strategy_id="stratB", strategy_name="B", signal_date=dates[0], codes=["000001"]),
            ]
        )
        cfg = _make_config(
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        result = BacktestEngine(cfg).run(signal_set, store)
        # 两个策略各自独立建仓、独立平仓 ⇒ 两笔成交
        assert len(result.trades) == 2
        assert {t.strategy for t in result.trades} == {"stratA", "stratB"}
        assert all(t.code == "000001" for t in result.trades)
        # 没有因去重产生的 skip
        assert result.skips == []

    def test_same_strategy_holding_blocks_second_signal(self):
        """同一策略在持仓期间再次选中同一票 → 不重复买，也不记 skip。"""
        dates = [date(2026, 7, i) for i in range(1, 15) if date(2026, 7, i).weekday() < 5]
        store = FakeMarketStore(self._rows(dates))
        # stratA 在 d0 和 d2 都选中 000001；d0 买入后持仓到 d6 才卖，
        # d2 的信号对应的买入日 d3 仍在持仓期内 → 应被静默跳过
        signal_set = SignalSet(
            signals=[
                StrategySignal(strategy_id="stratA", strategy_name="A", signal_date=dates[0], codes=["000001"]),
                StrategySignal(strategy_id="stratA", strategy_name="A", signal_date=dates[2], codes=["000001"]),
            ]
        )
        cfg = _make_config(
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
        )
        result = BacktestEngine(cfg).run(signal_set, store)
        assert len(result.trades) == 1
        assert result.trades[0].strategy == "stratA"
        assert result.skips == []

    def test_same_strategy_can_rebuy_after_sell(self):
        """卖出后再次被选中 → 可以再买（第二笔独立成交）。"""
        dates = [date(2026, 7, i) for i in range(1, 20) if date(2026, 7, i).weekday() < 5]
        store = FakeMarketStore(self._rows(dates))
        # d0 选中 → d1 买 → d6 卖；d7 选中 → d8 买 → d13 卖
        signal_set = SignalSet(
            signals=[
                StrategySignal(strategy_id="stratA", strategy_name="A", signal_date=dates[0], codes=["000001"]),
                StrategySignal(strategy_id="stratA", strategy_name="A", signal_date=dates[6], codes=["000001"]),
            ]
        )
        cfg = _make_config(
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
            execution={"fixed_hold_n_days": 5},
            portfolio={"allow_reentry_same_stock": True},
        )
        result = BacktestEngine(cfg).run(signal_set, store)
        assert len(result.trades) == 2
        assert all(t.strategy == "stratA" and t.code == "000001" for t in result.trades)
        # 两笔的买入日不同
        assert result.trades[0].buy_date != result.trades[1].buy_date


class TestMinimumLotForHighPrice:
    """`unlimited_cash` 的每笔名义额是"想投多少"，不是"买得起多少"。

    5 万 ÷ 1300 元的茅台凑不满一手，原行为是整笔放弃并在 skips 里挂一个
    "资金预算不足"——无限资金模式下这句话是假的。改成按最小成交单位买满
    （主板 100 股、科创板 200 股），名义额因此可以超过每笔预算。
    """

    @staticmethod
    def _dates() -> list[date]:
        return [date(2026, 7, i) for i in range(1, 20) if date(2026, 7, i).weekday() < 5]

    def _run(self, code: str, price: float, *, capital: dict):
        dates = self._dates()
        store = FakeMarketStore({
            code: [
                {"date": d, "open": price, "close": price,
                 "high": price * 1.01, "low": price * 0.99}
                for d in dates
            ]
        })
        signal_set = SignalSet(signals=[StrategySignal(
            strategy_id="stratA", strategy_name="A", signal_date=dates[0], codes=[code],
        )])
        cfg = _make_config(
            capital=capital,
            execution={"fixed_hold_n_days": 5},
        )
        return BacktestEngine(cfg).run(signal_set, store)

    def test_main_board_buys_one_lot_when_budget_is_short(self):
        result = self._run(
            "600519", 1300.0,
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
        )
        assert result.skips == []
        assert len(result.trades) == 1
        assert result.trades[0].shares == 100
        # buy_amount 含 2bp 买入滑点；凑不凑得满一手是按开盘价判的
        assert result.trades[0].buy_amount == pytest.approx(1300 * 100 * 1.0002)

    def test_star_market_buys_200_shares_minimum(self):
        """科创板最小成交单位是 200 股，不是一手 100 股。"""
        result = self._run(
            "688498", 1600.0,
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
        )
        assert result.skips == []
        assert result.trades[0].shares == 200
        assert result.trades[0].buy_amount == pytest.approx(1600 * 200 * 1.0002)

    def test_budget_sufficient_trade_is_unchanged(self):
        """名义额够买时一切照旧：5 万 ÷ 10 元 = 5000 股，不是最小单位 100 股。"""
        result = self._run(
            "000001", 10.0,
            capital={"mode": "unlimited_cash", "fixed_cash_per_trade": 50000},
        )
        assert result.trades[0].shares == 5000

    def test_realistic_mode_still_skips_unaffordable(self):
        """realistic 模式下"资金预算不足"是真话：账户确实掏不出 13 万，不能硬买。"""
        result = self._run(
            "600519", 1300.0,
            capital={"mode": "realistic", "initial_cash": 20000},
        )
        assert result.trades == []
        assert [s.reason for s in result.skips] == ["资金预算不足"]
