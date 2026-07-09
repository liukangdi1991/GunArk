from __future__ import annotations

import pytest

from trendradar.domain.backtest.config import CostConfig
from trendradar.domain.backtest.execution import (
    FillResult,
    calc_buy_fill,
    calc_sell_fill,
    is_limit_down,
    is_limit_up,
    limit_down_price,
    limit_up_price,
)


class TestLimitPrices:
    def test_limit_up_price_main_board(self):
        assert limit_up_price(10.0) == pytest.approx(11.0)

    def test_limit_up_price_st(self):
        assert limit_up_price(10.0, is_st=True) == pytest.approx(10.5)

    def test_limit_up_price_gem(self):
        assert limit_up_price(10.0, code="300001") == pytest.approx(12.0)

    def test_limit_up_price_star(self):
        assert limit_up_price(10.0, code="688001") == pytest.approx(12.0)

    def test_limit_up_price_bse(self):
        assert limit_up_price(10.0, code="430001") == pytest.approx(13.0)

    def test_limit_up_price_rounding(self):
        result = limit_up_price(9.99)
        assert result == pytest.approx(10.99)

    def test_limit_down_price_main_board(self):
        assert limit_down_price(10.0) == pytest.approx(9.0)

    def test_limit_down_price_st(self):
        assert limit_down_price(10.0, is_st=True) == pytest.approx(9.5)

    def test_limit_down_price_gem(self):
        assert limit_down_price(10.0, code="300001") == pytest.approx(8.0)

    def test_is_limit_up_positive(self):
        assert is_limit_up(11.0, 10.0)

    def test_is_limit_up_negative(self):
        assert not is_limit_up(10.99, 10.0)

    def test_is_limit_up_none_prev_close(self):
        assert not is_limit_up(11.0, None)

    def test_is_limit_up_zero_prev_close(self):
        assert not is_limit_up(11.0, 0.0)

    def test_is_limit_down_positive(self):
        assert is_limit_down(9.0, 10.0)

    def test_is_limit_down_negative(self):
        assert not is_limit_down(9.01, 10.0)

    def test_is_limit_down_none_prev_close(self):
        assert not is_limit_down(9.0, None)

    def test_is_limit_down_zero_prev_close(self):
        assert not is_limit_down(9.0, 0.0)


class TestFillCalculation:
    COSTS = CostConfig(
        commission_rate=0.0003,
        commission_min=5.0,
        stamp_duty_rate_sell=0.0001,
        transfer_fee_rate=0.00001,
        slippage_buy_bp=2.0,
        slippage_sell_bp=2.0,
    )

    def test_buy_fill_basic(self):
        fill = calc_buy_fill(10.0, 1000, self.COSTS)
        assert fill.price == pytest.approx(10.002)
        assert fill.amount == pytest.approx(10002.0)
        assert fill.fee > 0

    def test_sell_fill_basic(self):
        fill = calc_sell_fill(10.0, 1000, self.COSTS)
        assert fill.price == pytest.approx(9.998)
        assert fill.amount == pytest.approx(9998.0)
        assert fill.fee > 0

    def test_buy_fill_commission_min(self):
        fill = calc_buy_fill(10.0, 100, self.COSTS)
        total_fee = 10.002 * 100 * 0.0003  # should be < 5
        assert total_fee < 5.0
        assert fill.fee >= 5.0

    def test_sell_fill_includes_stamp_duty(self):
        fill_buy = calc_buy_fill(10.0, 1000, self.COSTS)
        fill_sell = calc_sell_fill(10.0, 1000, self.COSTS)
        assert fill_sell.fee > fill_buy.fee  # sell has stamp duty extra

    def test_fill_result_is_frozen(self):
        fill = calc_buy_fill(10.0, 100, self.COSTS)
        with pytest.raises(Exception):
            fill.price = 11.0
