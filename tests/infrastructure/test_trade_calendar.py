from datetime import date
import polars as pl
from trendradar.infrastructure.tushare.calendar import (
    load_trade_calendar,
    save_trade_calendar,
)


def test_save_and_load_roundtrip(tmp_path):
    dates = [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)]
    path = tmp_path / "trade_calendar.parquet"
    save_trade_calendar(path, dates)
    assert load_trade_calendar(path) == dates


def test_load_missing_returns_none(tmp_path):
    assert load_trade_calendar(tmp_path / "missing.parquet") is None


def test_save_is_atomic(tmp_path):
    path = tmp_path / "trade_calendar.parquet"
    save_trade_calendar(path, [date(2026, 8, 18)])
    save_trade_calendar(path, [date(2026, 8, 19)])
    assert load_trade_calendar(path) == [date(2026, 8, 19)]
