from datetime import date, datetime
from zoneinfo import ZoneInfo

from trendradar.domain.market.sync.planner import build_plan
from trendradar.domain.market.sync.spec import PlanKind

CN = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 8, 27)
NOW = datetime(2026, 8, 27, 18, 0, tzinfo=CN)  # 16:00 后，今日可得
CAL = {date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), date(2026, 12, 31)}
DONE_FULL = {date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)}


def test_row1_blocked_calendar_stale():
    plan = build_plan(date(2027, 1, 2), NOW, {date(2026, 12, 31)}, DONE_FULL, False, {})
    assert plan.kind is PlanKind.BLOCKED
    assert plan.latest_tradeable is None


def test_row1_blocked_calendar_empty():
    plan = build_plan(TODAY, NOW, set(), DONE_FULL, False, {})
    assert plan.kind is PlanKind.BLOCKED


def test_row2_codes_backfill():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, False, {"codes": ["000001"]})
    assert plan.kind is PlanKind.BACKFILL_CODES


def test_row3_force_full_even_when_suspect():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, True, {"force": True})
    assert plan.kind is PlanKind.FULL


def test_row4_first_build_full():
    plan = build_plan(TODAY, NOW, CAL, set(), False, {})
    assert plan.kind is PlanKind.FULL
    assert plan.reason == "首次建库"


def test_row5_suspect_rebuild_required_not_full():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, True, {})
    assert plan.kind is PlanKind.REBUILD_REQUIRED


def test_row6_uptodate():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, False, {})
    assert plan.kind is PlanKind.UPTODATE
    assert plan.missing_days == []
    assert plan.stale_days == 0


def test_row7_incremental_tail_gap():
    done = {date(2026, 8, 25)}
    plan = build_plan(TODAY, NOW, CAL, done, False, {})
    assert plan.kind is PlanKind.INCREMENTAL
    assert plan.missing_days == [date(2026, 8, 26), date(2026, 8, 27)]
    assert plan.stale_days == 2
    assert plan.latest_tradeable == date(2026, 8, 27)


def test_mid_hole_missing_greater_than_stale():
    # 日历 25/26/27；done 只有 25 与 27：中段洞 26 + 尾部 0
    done = {date(2026, 8, 25), date(2026, 8, 27)}
    plan = build_plan(TODAY, NOW, CAL, done, False, {})
    assert plan.kind is PlanKind.INCREMENTAL
    assert plan.missing_days == [date(2026, 8, 26)]
    assert plan.stale_days == 0
    assert len(plan.missing_days) > plan.stale_days


def test_stale_days_counts_consecutive_tail():
    cal = {date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24),
           date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27),
           date(2026, 12, 31)}
    done = {date(2026, 8, 20), date(2026, 8, 24)}
    plan = build_plan(TODAY, NOW, cal, done, False, {})
    assert plan.missing_days == [date(2026, 8, 21), date(2026, 8, 25),
                                 date(2026, 8, 26), date(2026, 8, 27)]
    assert plan.stale_days == 3  # 27/26/25 尾部连续，止于 24


def test_matrix_priority_codes_beats_force():
    plan = build_plan(TODAY, NOW, CAL, set(), False, {"codes": ["000001"], "force": True})
    assert plan.kind is PlanKind.BACKFILL_CODES
