"""Tests: backtest prerequisites validation.

A backtest needs the signal dates to have enough trading days after them to
buy (T+1) and hold (T+N+1). Signals on the last trading day(s) would produce
zero trades — the UI should block those instead of running a pointless job.
"""

from __future__ import annotations

from datetime import date, timedelta

from trendradar.domain.signal.models import SignalSet, StrategySignal


def _calendar(n: int, start: date = date(2026, 8, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _signal_set(signal_dates: list[date]) -> SignalSet:
    return SignalSet(
        signals=[
            StrategySignal(
                strategy_id="s1",
                strategy_name="S1",
                signal_date=d,
                codes=["000001"],
            )
            for d in signal_dates
        ]
    )


def _validate(
    signal_set: SignalSet,
    calendar: list[date],
    hold: int = 5,
    entry_on_signal_day: bool = False,
) -> list[str]:
    from trendradar.app.services.backtest_service import validate_backtest_prerequisites

    return validate_backtest_prerequisites(
        signal_set, calendar, fixed_hold_n_days=hold, entry_on_signal_day=entry_on_signal_day,
    )


def test_empty_signal_set_is_blocked():
    reasons = _validate(SignalSet(), _calendar(20))
    assert len(reasons) >= 1
    assert "空" in reasons[0]


def test_signal_date_missing_from_calendar_is_blocked():
    cal = _calendar(10)  # 2026-08-01 .. 2026-08-10
    signals = _signal_set([date(2026, 8, 15)])  # not a trading day in cal
    reasons = _validate(signals, cal)
    assert len(reasons) >= 1
    assert "2026-08-15" in reasons[0]


def test_signal_on_last_trading_day_is_blocked():
    cal = _calendar(10)
    signals = _signal_set([cal[-1]])
    reasons = _validate(signals, cal)
    assert len(reasons) >= 1
    assert "无足够交易日" in reasons[0]
    assert str(cal[-1]) in reasons[0]


def test_signal_with_insufficient_hold_days_is_blocked():
    cal = _calendar(10)
    # signal on second-to-last day: only 1 trading day left, need hold+1 = 6
    signals = _signal_set([cal[-2]])
    reasons = _validate(signals, cal)
    assert len(reasons) >= 1


def test_signal_with_enough_future_days_passes():
    cal = _calendar(30)
    signals = _signal_set([cal[5]])
    assert _validate(signals, cal) == []


def test_mixed_tradeable_and_tail_signals_pass():
    """Range selection with some tail signals still runs (tail ones are skipped)."""
    cal = _calendar(30)
    signals = _signal_set([cal[5], cal[-1]])
    assert _validate(signals, cal) == []


def test_hold_one_boundary():
    cal = _calendar(10)
    # hold=1: need sig_idx + 2 < len -> sig_idx <= 7 passes, 8 blocks
    assert _validate(_signal_set([cal[7]]), cal, hold=1) == []
    assert len(_validate(_signal_set([cal[8]]), cal, hold=1)) >= 1


def test_ultra_short_tail_signal_passes():
    """entry_on_signal_day + hold=1: signal on the second-to-last trading day
    still has room (entry same day, sell next) — must not be blocked."""
    cal = _calendar(10)
    signals = _signal_set([cal[-2]])
    assert _validate(signals, cal, hold=1, entry_on_signal_day=True) == []
