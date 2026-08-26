from datetime import date, datetime, timedelta, timezone

import pandas as pd
import polars as pl
import pytest

from trendradar.infrastructure.tushare.syncer import merge_day_bars, sync_market


@pytest.fixture(autouse=True)
def _hermetic_stock_list(monkeypatch):
    """新股补齐会读/拉股票列表——默认 mock 空列表保持 hermetic（显式 mock 的测试覆盖它）。"""
    import polars as pl

    import trendradar.infrastructure.tushare.stocklist as stocklist_mod

    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": []}),
    )


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

    def adj_factor(self, **kwargs):
        # 测试默认无复权因子：返回空 → 保留占位 1.0
        if "trade_date" in kwargs:
            return pl.DataFrame()
        if "ts_code" in kwargs:
            return pl.DataFrame()
        raise AssertionError("unexpected adj_factor args")


def _row(code, day, close=10.0):
    return {
        "ts_code": f"{code}.SZ", "trade_date": day.strftime("%Y%m%d"),
        "open": close, "high": close, "low": close, "close": close,
        "vol": 1000.0, "amount": 10000.0,
    }


def _bar_row(code, day, close, pre_close=None):
    r = {
        "code": code, "date": day, "open": close, "high": close, "low": close,
        "close": close, "volume": 1000.0, "amount": 10000.0,
        "adj_factor": 1.0, "is_suspended": False,
    }
    if pre_close is not None:
        r["pre_close"] = pre_close
    return r


def test_merge_day_bars_aligns_new_schema_columns(tmp_path):
    """升级兼容：旧 parquet 缺 pre_close 列，新数据带该列——合并不得崩溃。"""
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir(parents=True)
    old = pl.DataFrame([_bar_row("000001", date(2026, 8, 18), 10.0)])
    old.write_parquet(bars_dir / "000001.parquet")

    day_df = pl.DataFrame([
        _bar_row("000001", date(2026, 8, 19), 10.5, pre_close=10.0),
    ])
    written = merge_day_bars(day_df, bars_dir)
    assert written == ["000001"]

    merged = pl.read_parquet(bars_dir / "000001.parquet")
    assert merged.height == 2
    assert merged["date"].to_list() == [date(2026, 8, 18), date(2026, 8, 19)]


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


def test_sync_market_exclude_boards_filters_daily_rows(tmp_path):
    bars_dir = tmp_path / "bars"
    pro = FakePro(daily_by_day={
        "20260819": pl.DataFrame([
            _row("000001", date(2026, 8, 19)),
            _row("300001", date(2026, 8, 19)),
            _row("688001", date(2026, 8, 19)),
        ]),
    })
    result = sync_market(
        pro, bars_dir, tmp_path, {"exclude_boards": ["gem", "star"]},
        now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        progress=None, cancel_check=None,
    )
    assert result["mode"] == "incremental"
    assert (bars_dir / "000001.parquet").exists()
    assert not (bars_dir / "300001.parquet").exists()
    assert not (bars_dir / "688001.parquet").exists()


def test_sync_market_exclude_boards_filters_by_stock_codes(tmp_path, monkeypatch):
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    import trendradar.infrastructure.tushare.stocklist as stocklist_mod

    captured = {}

    def fake_sync_by_stock(pro, codes, *args, **kwargs):
        captured["codes"] = codes
        return {"failed_codes": []}

    monkeypatch.setattr(syncer_mod, "sync_by_stock", fake_sync_by_stock)
    meta = pl.DataFrame({"code": ["000001", "300001", "688001", "430001"]})
    monkeypatch.setattr(stocklist_mod, "sync_stock_list", lambda bars_dir: meta)

    bars_dir = tmp_path / "bars"
    pro = FakePro()
    result = sync_market(
        pro, bars_dir, tmp_path,
        {"exclude_boards": ["gem", "star", "bj"], "force": True},
        now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        progress=None, cancel_check=None,
    )
    assert result["mode"] == "full"
    assert captured["codes"] == ["000001"]


def test_sync_market_daily_exception_skips_without_done(tmp_path):
    """增量日路径接口异常：不崩溃、异常日不标 done（下轮重试可补）。"""
    bars_dir = tmp_path / "bars"

    class BoomPro(FakePro):
        def daily(self, **kwargs):
            if "trade_date" in kwargs:
                raise Exception("每分钟最多访问该接口")
            return super().daily(**kwargs)

    pro = BoomPro()
    result = sync_market(
        pro, bars_dir, tmp_path, {},
        now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        progress=None, cancel_check=None,
    )
    assert result["mode"] == "incremental"

    from trendradar.infrastructure.tushare.markers import load_sync_done
    done = load_sync_done(tmp_path / "sync_done.json")
    assert not any(d >= date(2026, 8, 18) for d in done)


def test_sync_by_stock_explicit_codes_not_overridden_by_retry(tmp_path, monkeypatch):
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    from trendradar.infrastructure.tushare.markers import save_retry_codes

    bars_dir = tmp_path / "bars"
    retry_path = tmp_path / "sync_retry_codes.json"
    done_path = tmp_path / "sync_done.json"
    save_retry_codes(retry_path, ["999999"])  # 上次失败的残留

    captured = []

    def fake_fetch(pro, code, seg_start, seg_end, retries, **kwargs):
        captured.append(code)
        return pl.DataFrame()

    monkeypatch.setattr(syncer_mod, "_fetch_with_retry", fake_fetch)
    pro = FakePro()
    syncer_mod.sync_by_stock(
        pro, ["000001"], date(2026, 8, 18), date(2026, 8, 20),
        bars_dir, done_path, retry_path, retry_failed=False,
    )
    # 显式指定的 codes 不能被残留 retry_codes 覆盖
    assert captured == ["000001"]


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


def test_sync_market_default_now_utc_does_not_crash(tmp_path, monkeypatch):
    """Regression: no now_utc passed -> datetime.now(timezone.utc) default path.

    Hermetic: 真实 now_utc 让缺口可能在 20 天阈值两侧翻转（by_stock 路径会真调
    Tushare stock_basic）——mock 股票列表，两条路径都不出网。
    """
    import polars as pl

    import trendradar.infrastructure.tushare.stocklist as stocklist_mod

    bars_dir = tmp_path / "bars"
    pro = FakePro(daily_by_day={})
    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": []}),
    )
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
                  progress=None, cancel_check=None, bucket=None, max_workers=6,
                  retry_failed=True):
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


def test_incremental_sync_catches_up_new_listings(tmp_path, monkeypatch):
    """日路径同步前自动补齐股票列表里有但无 bars 的新上市代码。"""
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    from datetime import date, datetime, timezone
    from unittest.mock import MagicMock

    import pandas as pd
    import polars as pl

    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    bars.mkdir(parents=True); cache.mkdir(parents=True)
    (bars / "000001.parquet").write_bytes(b"x")  # 已有代码

    days = [date(2026, 8, 20), date(2026, 8, 21)]
    pro = MagicMock()
    pro.trade_cal.return_value = pd.DataFrame({
        "cal_date": [d.strftime("%Y%m%d") for d in days],
        "is_open": [1] * len(days),
    })

    captured = {"codes": None}

    def fake_sync(pro, codes, start, end, bars_dir, done_path, retry_path,
                  progress=None, cancel_check=None, bucket=None, max_workers=6,
                  retry_failed=True):
        captured["codes"] = list(codes)
        for c in codes:
            (bars_dir / f"{c}.parquet").write_bytes(b"x")
        return {"failed_codes": []}

    monkeypatch.setattr(syncer_mod, "sync_by_stock", fake_sync)
    import trendradar.infrastructure.tushare.stocklist as stocklist_mod
    monkeypatch.setattr(
        stocklist_mod, "sync_stock_list",
        lambda bars_dir: pl.DataFrame({"code": ["000001", "920099"]}),
    )
    monkeypatch.setattr(syncer_mod, "_fetch_daily_by_date", lambda pro, day: pl.DataFrame())

    result = syncer_mod.sync_market(
        pro, bars, cache,
        {"start_date": days[0].isoformat(), "end_date": days[1].isoformat()},
        now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    )
    assert captured["codes"] == ["920099"]  # 只补新股，不重拉已有代码
    assert result["new_codes"] == 1


def test_merge_day_bars_aligns_column_order(tmp_path):
    """列序不匹配：旧 parquet 的 pre_close 在末尾，新数据在中间——合并不得崩溃。"""
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir(parents=True)
    # 旧文件（回填后）：pre_close 追加在末尾
    old = pl.DataFrame([
        _bar_row("000001", date(2026, 8, 18), 10.0, pre_close=10.0),
    ])
    assert old.columns[-1] == "pre_close", "pre_close 应在末尾（模拟回填后列序）"
    old.write_parquet(bars_dir / "000001.parquet")

    # 新数据（规范列序：pre_close 在 amount 与 adj_factor 之间）
    incoming = pl.DataFrame([{
        "code": "000001", "date": date(2026, 8, 19),
        "open": 10.5, "high": 10.5, "low": 10.5, "close": 10.5,
        "volume": 1000.0, "amount": 10000.0,
        "pre_close": 10.3, "adj_factor": 1.0, "is_suspended": False,
    }])
    assert incoming.columns.index("pre_close") < incoming.columns.index("adj_factor")

    written = merge_day_bars(incoming, bars_dir)
    assert written == ["000001"]
    merged = pl.read_parquet(bars_dir / "000001.parquet")
    assert merged.height == 2
    assert merged["date"].to_list() == [date(2026, 8, 18), date(2026, 8, 19)]
    assert merged["pre_close"].to_list() == [10.0, 10.3]


def test_attach_adj_factor_overrides_placeholder():
    """真实复权因子按 (code,date) 合并，覆盖占位 1.0。"""
    from trendradar.infrastructure.tushare.syncer import _attach_adj_factor

    df = pl.DataFrame([_bar_row("000001", date(2026, 8, 19), 10.5)])
    adj = pl.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20260819", "adj_factor": 2.5,
    }])
    out = _attach_adj_factor(df, adj)
    assert out["adj_factor"].to_list() == [2.5]


def test_attach_adj_factor_empty_keeps_placeholder():
    """无复权因子响应（停牌/数据缺失）：保留占位 1.0，不崩溃。"""
    from trendradar.infrastructure.tushare.syncer import _attach_adj_factor

    df = pl.DataFrame([_bar_row("000001", date(2026, 8, 19), 10.5)])
    out = _attach_adj_factor(df, pl.DataFrame())
    assert out["adj_factor"].to_list() == [1.0]
