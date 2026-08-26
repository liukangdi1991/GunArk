from datetime import date, datetime, timedelta, timezone

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.syncer import sync_market


class FakePro:
    """Synthetic market: 2 stocks, 3 trade days, evolving history."""

    def __init__(self, trade_days, data_by_day):
        self.trade_days = trade_days
        self.data_by_day = data_by_day
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

    def adj_factor(self, **kwargs):
        return pl.DataFrame()

    def daily(self, **kwargs):
        if "trade_date" in kwargs:
            self.daily_calls.append(kwargs["trade_date"])
            return self.data_by_day.get(kwargs["trade_date"], pl.DataFrame())
        raise AssertionError("by-stock path not expected")


def _row(code, day, close):
    return {"ts_code": f"{code}.SZ", "trade_date": day.strftime("%Y%m%d"),
            "open": close, "high": close, "low": close, "close": close,
            "vol": 1000.0, "amount": 10000.0}


def test_head_gap_backfill_regression(tmp_path, monkeypatch):
    """The exact bug from the spec: earlier start must backfill the head gap."""
    days = [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)]
    pro = FakePro(set(days), {
        d.strftime("%Y%m%d"): pl.DataFrame([_row("000001", d, 10.0)]) for d in days
    })
    bars_dir = tmp_path / "bars"
    cache = tmp_path / "cache"
    # 新股补齐会调 sync_stock_list（真实 Tushare）——mock 为空列表保持 hermetic
    import trendradar.infrastructure.tushare.stocklist as stocklist_mod
    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": []}),
    )

    # First run covers 08-18..08-20
    sync_market(pro, bars_dir, cache, {"start_date": "2026-08-18", "end_date": "2026-08-20"},
                now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                progress=None, cancel_check=None)
    # Second run requests earlier start 08-15 -> must backfill 08-15..08-17
    # (no trade days there in this synthetic cal, but the *request* must not
    # short-circuit; add 08-15 to the cal to prove backfill)
    pro.trade_days.add(date(2026, 8, 15))
    pro.data_by_day["20260815"] = pl.DataFrame([_row("000001", date(2026, 8, 15), 9.5)])
    result = sync_market(pro, bars_dir, cache,
                         {"start_date": "2026-08-15", "end_date": "2026-08-21"},
                         now_utc=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                         progress=None, cancel_check=None)
    assert result["skipped_uptodate"] is False
    df = pl.read_parquet(bars_dir / "000001.parquet")
    assert date(2026, 8, 15) in df["date"].to_list()
