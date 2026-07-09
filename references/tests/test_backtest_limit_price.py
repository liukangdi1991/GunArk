from __future__ import annotations

from backtest.execution import is_limit_down, is_limit_up


def test_close_equal_low_is_not_limit_down_without_reaching_down_limit_price() -> None:
    assert is_limit_down(price=14.88, prev_close=15.39, code="300341") is False


def test_gem_stock_uses_twenty_percent_limit() -> None:
    assert is_limit_down(price=8.0, prev_close=10.0, code="300341") is True
    assert is_limit_up(price=12.0, prev_close=10.0, code="300341") is True


def test_main_board_stock_uses_ten_percent_limit() -> None:
    assert is_limit_down(price=9.0, prev_close=10.0, code="600000") is True
    assert is_limit_up(price=11.0, prev_close=10.0, code="600000") is True
