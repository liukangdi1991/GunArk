from __future__ import annotations

from datetime import date

import pytest

from trendradar.domain.backtest.models import Position
from trendradar.domain.backtest.portfolio import (
    PortfolioState,
    available_open_slots,
    current_equity,
    filter_reentry_keys,
    is_holding,
)


def _make_position(code: str = "000001", entry_cost: float = 50000.0) -> Position:
    return Position(
        strategy="test_strategy",
        code=code,
        signal_date=date(2026, 7, 1),
        entry_date=date(2026, 7, 2),
        target_sell_date=date(2026, 7, 9),
        entry_price=10.0,
        shares=5000,
        entry_cost=entry_cost,
    )


class TestPortfolioState:
    S = "test_strategy"

    def test_initial_state(self):
        state = PortfolioState(cash=100000.0)
        assert state.cash == 100000.0
        assert state.positions == {}
        assert state.closed_keys == set()

    def test_open_position(self):
        state = PortfolioState(cash=100000.0)
        pos = _make_position("000001", entry_cost=50000.0)
        state.open_position("000001", self.S, pos, cash_used=50000.0)
        assert ("000001", self.S) in state.positions
        assert state.cash == 50000.0
        assert state.positions[("000001", self.S)].code == "000001"

    def test_open_multiple_positions(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 30000.0), 30000.0)
        state.open_position("600519", self.S, _make_position("600519", 40000.0), 40000.0)
        assert len(state.positions) == 2
        assert state.cash == 30000.0

    def test_close_position(self):
        state = PortfolioState(cash=100000.0)
        pos = _make_position("000001", entry_cost=50000.0)
        state.open_position("000001", self.S, pos, cash_used=50000.0)
        returned = state.close_position("000001", self.S, cash_back=55000.0)
        assert ("000001", self.S) not in state.positions
        assert state.cash == 105000.0
        assert returned.code == "000001"

    def test_close_position_adds_to_closed_keys(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 50000.0), 50000.0)
        state.close_position("000001", self.S, cash_back=52000.0)
        assert ("000001", self.S) in state.closed_keys

    def test_close_position_key_error(self):
        state = PortfolioState(cash=100000.0)
        with pytest.raises(KeyError):
            state.close_position("nonexistent", self.S, cash_back=0.0)

    def test_available_open_slots_no_limit(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 50000.0), 50000.0)
        assert available_open_slots(state, None) >= 100

    def test_available_open_slots_with_limit(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 50000.0), 50000.0)
        assert available_open_slots(state, 3) == 2

    def test_available_open_slots_full(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 50000.0), 50000.0)
        state.open_position("600519", self.S, _make_position("600519", 40000.0), 40000.0)
        assert available_open_slots(state, 2) == 0

    def test_is_holding_true(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", 50000.0), 50000.0)
        assert is_holding(state, "000001", self.S)

    def test_is_holding_false(self):
        state = PortfolioState(cash=100000.0)
        assert not is_holding(state, "000001", self.S)

    def test_filter_reentry_keys_allow(self):
        state = PortfolioState(cash=100000.0)
        state.closed_keys.add(("000001", self.S))
        keys = [("000001", self.S), ("600519", self.S)]
        assert filter_reentry_keys(state, keys, allow_reentry=True) == keys

    def test_filter_reentry_keys_block(self):
        state = PortfolioState(cash=100000.0)
        state.closed_keys.add(("000001", self.S))
        keys = [("000001", self.S), ("600519", self.S)]
        assert filter_reentry_keys(state, keys, allow_reentry=False) == [("600519", self.S)]

    def test_current_equity_no_positions(self):
        state = PortfolioState(cash=100000.0)
        eq = current_equity(state, {})
        assert eq == 100000.0

    def test_current_equity_with_positions(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", entry_cost=50000.0), 50000.0)
        eq = current_equity(state, {("000001", self.S): 12.0})
        assert eq == pytest.approx(50000.0 + 12.0 * 5000)

    def test_current_equity_fallback_entry_price(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", self.S, _make_position("000001", entry_cost=50000.0), 50000.0)
        eq = current_equity(state, {})
        assert eq == pytest.approx(50000.0 + 10.0 * 5000)


class TestPositionKeyIsCodeStrategy:
    """持仓键是 (code, strategy)：一个策略一只股票同时只持一份，不同策略互不干扰。"""

    def _pos(self, code: str, strategy: str, entry_cost: float = 50000.0) -> Position:
        return Position(
            strategy=strategy,
            code=code,
            signal_date=date(2026, 7, 1),
            entry_date=date(2026, 7, 2),
            target_sell_date=date(2026, 7, 9),
            entry_price=10.0,
            shares=5000,
            entry_cost=entry_cost,
        )

    def test_two_strategies_hold_same_code_simultaneously(self):
        state = PortfolioState(cash=200000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        state.open_position("000001", "stratB", self._pos("000001", "stratB"), 50000.0)
        assert len(state.positions) == 2
        assert ("000001", "stratA") in state.positions
        assert ("000001", "stratB") in state.positions
        assert state.cash == 100000.0

    def test_is_holding_is_per_strategy(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        assert is_holding(state, "000001", "stratA")
        assert not is_holding(state, "000001", "stratB")

    def test_close_one_strategy_keeps_the_other(self):
        state = PortfolioState(cash=200000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        state.open_position("000001", "stratB", self._pos("000001", "stratB"), 50000.0)
        returned = state.close_position("000001", "stratA", cash_back=55000.0)
        assert returned.strategy == "stratA"
        assert ("000001", "stratA") not in state.positions
        assert ("000001", "stratB") in state.positions
        assert state.cash == 155000.0

    def test_closed_keys_records_code_strategy_pair(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        state.close_position("000001", "stratA", cash_back=52000.0)
        assert ("000001", "stratA") in state.closed_keys
        assert ("000001", "stratB") not in state.closed_keys

    def test_reopen_same_key_after_close(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        state.close_position("000001", "stratA", cash_back=52000.0)
        # 卖了再选出来 → 可以再买
        state.open_position("000001", "stratA", self._pos("000001", "stratA"), 50000.0)
        assert ("000001", "stratA") in state.positions

    def test_filter_reentry_keys_blocks_only_closed_key(self):
        state = PortfolioState(cash=100000.0)
        state.closed_keys.add(("000001", "stratA"))
        keys = [("000001", "stratA"), ("000001", "stratB"), ("600519", "stratA")]
        assert filter_reentry_keys(state, keys, allow_reentry=False) == [
            ("000001", "stratB"),
            ("600519", "stratA"),
        ]
        assert filter_reentry_keys(state, keys, allow_reentry=True) == keys

    def test_current_equity_uses_position_keyed_marks(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", "stratA", self._pos("000001", "stratA", 50000.0), 50000.0)
        state.open_position("000001", "stratB", self._pos("000001", "stratB", 50000.0), 50000.0)
        # mark_prices 按 (code, strategy) 查，两份持仓各自标记
        eq = current_equity(state, {("000001", "stratA"): 12.0, ("000001", "stratB"): 12.0})
        assert eq == pytest.approx(12.0 * 5000 * 2)
