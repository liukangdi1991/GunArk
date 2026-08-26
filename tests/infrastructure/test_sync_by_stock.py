import datetime
from datetime import date

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.syncer import sync_by_stock


class FakePro:
    def __init__(self, ranges):
        self.ranges = ranges  # code -> list of (start, end) requested
        self.calls = []

    def trade_cal(self, exchange, start_date, end_date):
        s = date.fromisoformat(
            f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        )
        e = date.fromisoformat(
            f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        )
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": 1})
            d += datetime.timedelta(days=1)
        return pd.DataFrame(rows)

    def adj_factor(self, **kwargs):
        return pl.DataFrame()

    def daily(self, **kwargs):
        self.calls.append(kwargs)
        code = kwargs["ts_code"]
        s = date.fromisoformat(
            kwargs["start_date"][:4] + "-" + kwargs["start_date"][4:6] + "-" + kwargs["start_date"][6:]
        )
        e = date.fromisoformat(
            kwargs["end_date"][:4] + "-" + kwargs["end_date"][4:6] + "-" + kwargs["end_date"][6:]
        )
        dates = []
        d = s
        while d <= e:
            dates.append(d)
            d += datetime.timedelta(days=1)
        return pd.DataFrame({
            "ts_code": [code] * len(dates),
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "open": [10.0] * len(dates), "high": [10.0] * len(dates),
            "low": [10.0] * len(dates), "close": [10.0] * len(dates),
            "vol": [1000.0] * len(dates), "amount": [10000.0] * len(dates),
        })


def test_sync_by_stock_writes_files_and_marks_done(tmp_path):
    bars_dir = tmp_path / "bars"
    done_path = tmp_path / "sync_done.json"
    retry_path = tmp_path / "sync_retry_codes.json"
    pro = FakePro({})
    result = sync_by_stock(
        pro, ["000001"], date(2026, 8, 18), date(2026, 8, 20),
        bars_dir, done_path, retry_path,
        progress=None, cancel_check=None,
    )
    assert result["failed_codes"] == []
    assert (bars_dir / "000001.parquet").exists()
    from trendradar.infrastructure.tushare.markers import load_sync_done
    assert load_sync_done(done_path) == {
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
    }


def test_sync_by_stock_partial_failure_marks_retry(tmp_path):
    class FailingPro(FakePro):
        def daily(self, **kwargs):
            if kwargs["ts_code"] == "000001.SZ":
                raise RuntimeError("boom")
            return super().daily(**kwargs)

    bars_dir = tmp_path / "bars"
    done_path = tmp_path / "sync_done.json"
    retry_path = tmp_path / "sync_retry_codes.json"
    pro = FailingPro({})
    result = sync_by_stock(
        pro, ["000001", "000002"], date(2026, 8, 18), date(2026, 8, 20),
        bars_dir, done_path, retry_path,
        progress=None, cancel_check=None,
    )
    assert result["failed_codes"] == ["000001"]
    from trendradar.infrastructure.tushare.markers import (
        load_retry_codes, load_sync_done,
    )
    assert load_retry_codes(retry_path) == ["000001"]
    assert load_sync_done(done_path) == set()  # partial failure -> no day marked
    assert (bars_dir / "000002.parquet").exists()
