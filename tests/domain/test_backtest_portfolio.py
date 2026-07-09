from __future__ import annotations

from datetime import date

import pytest

from trendradar.domain.backtest.models import Position
from trendradar.domain.backtest.portfolio import (
    PortfolioState,
    available_open_slots,
    current_equity,
    filter_reentry_codes,
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
    def test_initial_state(self):
        state = PortfolioState(cash=100000.0)
        assert state.cash == 100000.0
        assert state.positions == {}
        assert state.closed_codes == set()

    def test_open_position(self):
        state = PortfolioState(cash=100000.0)
        pos = _make_position("000001", entry_cost=50000.0)
        state.open_position("000001", pos, cash_used=50000.0)
        assert "000001" in state.positions
        assert state.cash == 50000.0
        assert state.positions["000001"].code == "000001"

    def test_open_multiple_positions(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 30000.0), 30000.0)
        state.open_position("600519", _make_position("600519", 40000.0), 40000.0)
        assert len(state.positions) == 2
        assert state.cash == 30000.0

    def test_close_position(self):
        state = PortfolioState(cash=100000.0)
        pos = _make_position("000001", entry_cost=50000.0)
        state.open_position("000001", pos, cash_used=50000.0)
        returned = state.close_position("000001", cash_back=55000.0)
        assert "000001" not in state.positions
        assert state.cash == 105000.0
        assert returned.code == "000001"

    def test_close_position_adds_to_closed_codes(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 50000.0), 50000.0)
        state.close_position("000001", cash_back=52000.0)
        assert "000001" in state.closed_codes

    def test_close_position_key_error(self):
        state = PortfolioState(cash=100000.0)
        with pytest.raises(KeyError):
            state.close_position("nonexistent", cash_back=0.0)

    def test_available_open_slots_no_limit(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 50000.0), 50000.0)
        assert available_open_slots(state, None) >= 100

    def test_available_open_slots_with_limit(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 50000.0), 50000.0)
        assert available_open_slots(state, 3) == 2

    def test_available_open_slots_full(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 50000.0), 50000.0)
        state.open_position("600519", _make_position("600519", 40000.0), 40000.0)
        assert available_open_slots(state, 2) == 0

    def test_is_holding_true(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", 50000.0), 50000.0)
        assert is_holding(state, "000001")

    def test_is_holding_false(self):
        state = PortfolioState(cash=100000.0)
        assert not is_holding(state, "000001")

    def test_filter_reentry_codes_allow(self):
        state = PortfolioState(cash=100000.0)
        state.closed_codes.add("000001")
        result = filter_reentry_codes(state, ["000001", "600519"], allow_reentry=True)
        assert result == ["000001", "600519"]

    def test_filter_reentry_codes_block(self):
        state = PortfolioState(cash=100000.0)
        state.closed_codes.add("000001")
        result = filter_reentry_codes(state, ["000001", "600519"], allow_reentry=False)
        assert result == ["600519"]

    def test_current_equity_no_positions(self):
        state = PortfolioState(cash=100000.0)
        eq = current_equity(state, {})
        assert eq == 100000.0

    def test_current_equity_with_positions(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", entry_cost=50000.0), 50000.0)
        mark_prices = {"000001": 12.0}
        eq = current_equity(state, mark_prices)
        assert eq == pytest.approx(50000.0 + 12.0 * 5000)

    def test_current_equity_fallback_entry_price(self):
        state = PortfolioState(cash=100000.0)
        state.open_position("000001", _make_position("000001", entry_cost=50000.0), 50000.0)
        eq = current_equity(state, {})
        assert eq == pytest.approx(50000.0 + 10.0 * 5000)
