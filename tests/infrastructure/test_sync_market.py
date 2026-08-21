from datetime import date, datetime, timedelta, timezone

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.syncer import sync_market


class FakePro:
    def __init__(self, daily_by_day=None, trade_days=None):
        self.daily_by_day = daily_by_day or {}
        self.trade_days = trade_days or {
            date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        }
        self.daily_calls = []

    def trade_cal(self, exchange, start_date, end_date):
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": int(d in self.trade_days)})
            d += timedelta(days=1)
        return pd.DataFrame(rows)

    def daily(self, **kwargs):
        if "trade_date" in kwargs:
            self.daily_calls.append(kwargs["trade_date"])
            return self.daily_by_day.get(kwargs["trade_date"], pl.DataFrame())
        raise AssertionError("by-stock path not expected")


def _row(code, day, close=10.0):
    return {
        "ts_code": f"{code}.SZ", "trade_date": day.strftime("%Y%m%d"),
        "open": close, "high": close, "low": close, "close": close,
        "vol": 1000.0, "amount": 10000.0,
    }


def test_sync_market_small_gap_uses_daily_path(tmp_path):
    bars_dir = tmp_path / "bars"
    pro = FakePro(daily_by_day={
        "20260819": pl.DataFrame([_row("000001", date(2026, 8, 19))]),
    })
    result = sync_market(
        pro, bars_dir, tmp_path, {},
        now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        progress=None, cancel_check=None,
    )
    assert result["mode"] == "incremental"
    assert result["synced_days"] == 1
    assert (bars_dir / "000001.parquet").exists()


def test_sync_market_up_to_date_zero_daily_calls(tmp_path):
    bars_dir = tmp_path / "bars"
    # Seed all days via a first run, then second run should not call daily
    pro = FakePro(daily_by_day={
        "20260818": pl.DataFrame([_row("000001", date(2026, 8, 18))]),
        "20260819": pl.DataFrame([_row("000001", date(2026, 8, 19))]),
        "20260820": pl.DataFrame([_row("000001", date(2026, 8, 20))]),
    })
    sync_market(pro, bars_dir, tmp_path, {},
                now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                progress=None, cancel_check=None)
    pro.daily_calls.clear()
    result = sync_market(pro, bars_dir, tmp_path, {},
                         now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                         progress=None, cancel_check=None)
    assert result["skipped_uptodate"] is True
    assert pro.daily_calls == []
