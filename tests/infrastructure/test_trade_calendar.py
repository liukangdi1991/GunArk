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


class YearShardingPro:
    def __init__(self):
        self.calls = []

    def trade_cal(self, exchange, start_date, end_date):
        self.calls.append((start_date, end_date))
        import pandas as pd
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": 1})
            d += __import__("datetime").timedelta(days=1)
        return pd.DataFrame(rows)


def test_fetch_shards_by_year(tmp_path):
    """Regression: a multi-year range must be sharded (row ceiling is 6000)."""
    from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar

    pro = YearShardingPro()
    dates = fetch_trade_calendar(pro, date(2020, 1, 1), date(2026, 8, 21))
    assert len(pro.calls) == 7  # one call per year
    assert date(2020, 1, 1) in dates
    assert date(2026, 8, 21) in dates
    assert dates == sorted(dates)
