from datetime import date
from trendradar.infrastructure.tushare.markers import (
    load_sync_done,
    save_sync_done,
    load_retry_codes,
    save_retry_codes,
)


def test_sync_done_roundtrip(tmp_path):
    path = tmp_path / "sync_done.json"
    dates = {date(2026, 8, 18), date(2026, 8, 20)}
    save_sync_done(path, dates)
    assert load_sync_done(path) == dates


def test_sync_done_missing_returns_empty(tmp_path):
    assert load_sync_done(tmp_path / "missing.json") == set()


def test_retry_codes_roundtrip(tmp_path):
    path = tmp_path / "sync_retry_codes.json"
    save_retry_codes(path, ["000001", "600519"])
    assert load_retry_codes(path) == ["000001", "600519"]


def test_retry_codes_missing_returns_empty(tmp_path):
    assert load_retry_codes(tmp_path / "missing.json") == []
