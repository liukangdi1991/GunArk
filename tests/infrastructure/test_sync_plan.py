"""decide_mode + plan_sync: mode decision and planning (no network)."""
from datetime import date, datetime, timedelta, timezone

from trendradar.infrastructure.tushare.syncer import decide_mode, plan_sync


def test_retry_codes_force_full():
    assert decide_mode(missing_days=1, force=False, retry_codes=["000001"]) == "full"


def test_force_always_full():
    assert decide_mode(missing_days=0, force=True, retry_codes=[]) == "full"


def test_large_gap_is_full():
    assert decide_mode(missing_days=21, force=False, retry_codes=[]) == "full"


def test_small_gap_is_incremental():
    assert decide_mode(missing_days=20, force=False, retry_codes=[]) == "incremental"


def test_zero_gap_is_incremental():
    assert decide_mode(missing_days=0, force=False, retry_codes=[]) == "incremental"


class FakePro:
    def __init__(self, trade_days):
        self._days = trade_days

    def trade_cal(self, **kwargs):
        import pandas as pd
        return pd.DataFrame({
            "cal_date": [d.strftime("%Y%m%d") for d in self._days],
            "is_open": [1] * len(self._days),
        })


class FailingCalendarPro:
    def trade_cal(self, **kwargs):
        raise Exception("network down")


def _run_plan(tmp_path, trade_days, request):
    bars = tmp_path / "bars"
    cache = tmp_path / "cache"
    return plan_sync(
        FakePro(trade_days), bars, cache, request,
        now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    )


def test_plan_large_gap_is_full(tmp_path):
    # 25 missing trade days (>20) → full mode
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(25)]
    p = _run_plan(tmp_path, days, {
        "start_date": days[0].isoformat(),
        "end_date": days[-1].isoformat(),
    })
    assert p.mode == "full"
    assert p.missing_days == 25
    assert p.uptodate is False


def test_plan_uptodate_when_done(tmp_path):
    import json
    days = [date(2026, 8, 3), date(2026, 8, 4)]
    bars = tmp_path / "bars"
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    (cache / "sync_done.json").write_text(
        json.dumps({"dates": ["2026-08-03", "2026-08-04"]})
    )
    p = plan_sync(
        FakePro(days), bars, cache,
        {"start_date": "2026-08-03", "end_date": "2026-08-04"},
        now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    )
    assert p.uptodate is True
    assert p.missing_days == 0


def test_plan_raises_when_calendar_unavailable(tmp_path):
    import pytest
    bars = tmp_path / "bars"
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="trade calendar unavailable"):
        plan_sync(
            FailingCalendarPro(), bars, cache,
            {"start_date": "2026-08-03", "end_date": "2026-08-05"},
        )
