from datetime import date, datetime
from zoneinfo import ZoneInfo

from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    DATA_CUTOFF_HOUR,
    FailureKind,
    PlanKind,
    SyncPlan,
    latest_tradeable_day,
)

CN = ZoneInfo("Asia/Shanghai")


def test_constants():
    assert BASELINE_START == date(2015, 1, 1)
    assert DATA_CUTOFF_HOUR == 16


def test_enum_values():
    assert PlanKind.BLOCKED.value == "blocked"
    assert PlanKind.REBUILD_REQUIRED.value == "rebuild_required"
    assert PlanKind.BACKFILL_CODES.value == "backfill_codes"
    assert FailureKind.ENV.value == "env"
    assert FailureKind.OK_EMPTY.value == "ok_empty"


def test_sync_plan_is_frozen():
    p = SyncPlan(kind=PlanKind.UPTODATE, latest_tradeable=None,
                 missing_days=[], stale_days=0, reason="x")
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.reason = "y"


def test_latest_tradeable_before_cutoff_is_previous_trade_day():
    days = {date(2026, 8, 26), date(2026, 8, 27)}
    now = datetime(2026, 8, 27, 10, 0, tzinfo=CN)
    assert latest_tradeable_day(days, now) == date(2026, 8, 26)


def test_latest_tradeable_at_cutoff_on_trade_day_is_today():
    days = {date(2026, 8, 26), date(2026, 8, 27)}
    now = datetime(2026, 8, 27, 16, 0, tzinfo=CN)
    assert latest_tradeable_day(days, now) == date(2026, 8, 27)


def test_latest_tradeable_weekend_is_friday():
    days = {date(2026, 8, 28), date(2026, 8, 31)}  # 周五 / 下周一
    now = datetime(2026, 8, 29, 10, 0, tzinfo=CN)  # 周六
    assert latest_tradeable_day(days, now) == date(2026, 8, 28)


def test_latest_tradeable_empty_calendar_is_none():
    assert latest_tradeable_day(set(), datetime(2026, 8, 27, 16, 0, tzinfo=CN)) is None
