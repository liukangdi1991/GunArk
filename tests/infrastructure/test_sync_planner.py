from datetime import date, datetime, timezone

from trendradar.infrastructure.tushare.syncer import (
    latest_tradeable_day,
    is_up_to_date,
    missing_trade_days,
    shard_ranges,
)

TRADE = {
    date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
    date(2026, 8, 21), date(2026, 8, 24),
}


def test_latest_tradeable_before_16():
    # Beijing 2026-08-21 10:00 -> not past 16:00, latest tradeable = 08-20
    now = datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 20)


def test_latest_tradeable_after_16():
    now = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)  # Beijing 17:00
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 21)


def test_latest_tradeable_non_trading_today():
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)  # Saturday
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 21)


def test_is_up_to_date_ok():
    done = {date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)}
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=None, latest=date(2026, 8, 20), done=done, trade_days=TRADE,
    ) is True


def test_is_up_to_date_missing_middle_day():
    done = {date(2026, 8, 18), date(2026, 8, 20)}  # 08-19 failed
    # max_date is latest but 08-19 not done -> not up to date
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=None, latest=date(2026, 8, 20), done=done, trade_days=TRADE,
    ) is False


def test_is_up_to_date_earlier_start_request():
    # req_start earlier than local_min -> head gap, not up to date
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=date(2026, 8, 1), latest=date(2026, 8, 20),
        done=set(), trade_days=TRADE,
    ) is False


def test_missing_trade_days():
    done = {date(2026, 8, 18), date(2026, 8, 20)}
    missing = missing_trade_days(
        TRADE, done, start=date(2026, 8, 18), end=date(2026, 8, 21)
    )
    assert missing == [date(2026, 8, 19), date(2026, 8, 21)]


def test_shard_ranges_small():
    ranges = shard_ranges(date(2026, 1, 1), date(2026, 1, 31))
    assert ranges == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_shard_ranges_large():
    # ~35 years -> multiple shards, each covering < 5500 rows
    ranges = shard_ranges(date(1990, 1, 1), date(2026, 8, 20))
    assert len(ranges) > 1
    for s, e in ranges:
        est_days = (e - s).days / 7 * 5
        assert est_days <= 5500
