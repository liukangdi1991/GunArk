"""交易策略 → 执行参数映射的契约。

1/2 号策略（多空线止损、10日低点止损）与超短线共用同一入场：
信号日当日收盘买入（涨停按当日收盘判定，拒绝追涨停）；差异只在持有与止损。
"""

from trendradar.interfaces.api.presenters import _trade_strategy_execution


def test_long_and_ten_day_stop_enter_on_signal_day_close():
    for name in ("long_term_bull_bear_stop", "ten_day_low_stop"):
        execution = _trade_strategy_execution(name)
        assert execution["entry_on_signal_day"] is True, name
        assert execution["entry_at_close"] is True, name


def test_original_exit_semantics_unchanged():
    bull_bear = _trade_strategy_execution("long_term_bull_bear_stop")
    assert bull_bear["force_sell_on_two_day_close_below_long_term_bull_bear_line"] is True
    assert "close_below_recent_low_stop_window" not in bull_bear

    ten_day = _trade_strategy_execution("ten_day_low_stop")
    assert ten_day["close_below_recent_low_stop_window"] == 10
    assert "force_sell_on_two_day_close_below_long_term_bull_bear_line" not in ten_day


def test_ultra_short_unchanged():
    execution = _trade_strategy_execution("ultra_short")
    assert execution == {
        "entry_on_signal_day": True,
        "entry_at_close": True,
        "fixed_hold_n_days": 1,
    }


def test_unknown_trade_strategy_yields_no_overrides():
    assert _trade_strategy_execution("nope") == {}
    assert _trade_strategy_execution(None) == {}
