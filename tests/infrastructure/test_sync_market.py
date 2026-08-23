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


def test_sync_market_default_now_utc_does_not_crash(tmp_path):
    """Regression: no now_utc passed -> datetime.now(timezone.utc) default path."""
    bars_dir = tmp_path / "bars"
    pro = FakePro(daily_by_day={})
    result = sync_market(pro, bars_dir, tmp_path, {}, progress=None, cancel_check=None)
    assert "mode" in result


def test_full_mode_retries_failed_codes(tmp_path, monkeypatch):
    """mock sync_by_stock: round 1 fails 2 codes, round 2 succeeds all."""
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    from datetime import date, datetime, timedelta, timezone
    from unittest.mock import MagicMock

    import pandas as pd
    import polars as pl

    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    bars.mkdir(parents=True); cache.mkdir(parents=True)

    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(25)]
    pro = MagicMock()
    pro.trade_cal.return_value = pd.DataFrame({
        "cal_date": [d.strftime("%Y%m%d") for d in days],
        "is_open": [1] * len(days),
    })

    calls = {"n": 0}

    def fake_sync(pro, codes, start, end, bars_dir, done_path, retry_path,
                  progress=None, cancel_check=None, bucket=None, max_workers=6):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"failed_codes": ["000002", "000003"]}
        return {"failed_codes": []}

    monkeypatch.setattr(syncer_mod, "sync_by_stock", fake_sync)
    import trendradar.infrastructure.tushare.stocklist as stocklist_mod
    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": ["000001", "000002", "000003"]}),
    )

    result = syncer_mod.sync_market(
        pro, bars, cache,
        {"start_date": days[0].isoformat(), "end_date": "2026-08-05"},
        now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
        retry_interval=0, max_retry_rounds=9,
    )
    assert result["mode"] == "full"
    assert result["failed_codes"] == 0  # count of remaining failures (int)
    assert result["retry_rounds"] == 1
    assert calls["n"] == 2


def test_sync_market_codes_param_restricts_and_bypasses_uptodate(tmp_path, monkeypatch):
    """codes 参数: 只同步指定代码，且不被 up-to-date 短路跳过。"""
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    from datetime import date, datetime, timedelta, timezone
    from unittest.mock import MagicMock

    import pandas as pd
    import polars as pl

    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    bars.mkdir(parents=True); cache.mkdir(parents=True)

    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(3)]
    pro = MagicMock()
    pro.trade_cal.return_value = pd.DataFrame({
        "cal_date": [d.strftime("%Y%m%d") for d in days],
        "is_open": [1] * len(days),
    })

    captured = {"codes": None}

    def fake_sync(pro, codes, start, end, bars_dir, done_path, retry_path,
                  progress=None, cancel_check=None, bucket=None, max_workers=6):
        captured["codes"] = list(codes)
        return {"failed_codes": []}

    monkeypatch.setattr(syncer_mod, "sync_by_stock", fake_sync)
    import trendradar.infrastructure.tushare.stocklist as stocklist_mod
    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": ["000001", "920099", "600519"]}),
    )

    # 日期已标记 done（up-to-date），但指定 codes 时必须绕过短路去拉
    import json as _json
    (cache / "sync_done.json").write_text(
        _json.dumps({"dates": [d.isoformat() for d in days]}))

    result = syncer_mod.sync_market(
        pro, bars, cache,
        {"codes": ["920099"], "start_date": days[0].isoformat(), "end_date": days[-1].isoformat()},
        now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    )
    assert result["skipped_uptodate"] is False
    assert captured["codes"] == ["920099"]
