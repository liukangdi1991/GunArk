from datetime import date

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.stocklist import (
    build_effective_list,
    normalize_stock_meta,
)

META = pl.DataFrame({
    "ts_code": ["000001.SZ", "000004.SZ", "920099.BJ", "300001.SZ", "688001.SH", "301999.SZ"],
    "code": ["000001", "000004", "920099", "300001", "688001", "301999"],
    "name": ["平安银行", "国华网安", "瑞华技术", "特锐德", "华兴源创", "未来上市"],
    "list_date": [date(1991, 4, 3), date(1991, 1, 14), date(2020, 7, 27),
                  date(2009, 10, 30), date(2019, 7, 22), date(2027, 1, 1)],
    "delist_date": [None, date(2026, 7, 13), None, None, None, None],
})

LATEST = date(2026, 8, 27)


def test_effective_list_excludes_bj_and_future_listed():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert "920099" not in eff.codes          # .BJ 永久剔除
    assert "301999" not in eff.codes          # list_date > latest
    assert set(eff.codes) == {"000001", "000004", "300001", "688001"}


def test_effective_list_exclude_boards_gem_star():
    eff = build_effective_list(META, exclude_boards=["gem", "star"], latest_tradeable=LATEST)
    assert set(eff.codes) == {"000001", "000004"}


def test_effective_expected_on_handles_delisting():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    # 2026-07-13 之前：4 只应市；退市日当天仍计；之后 3 只
    assert eff.expected_on(date(2026, 7, 10)) == 4
    assert eff.expected_on(date(2026, 7, 13)) == 4
    assert eff.expected_on(date(2026, 7, 14)) == 3


def test_effective_clamped_range():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert eff.clamped_range("000001") == (date(2015, 1, 1), LATEST)      # BASELINE 钳制
    assert eff.clamped_range("000004") == (date(2015, 1, 1), date(2026, 7, 13))  # 退市钳制
    assert eff.clamped_range("920099") is None


def test_normalize_meta_parses_dates_and_empty_delist():
    raw = pd.DataFrame([
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行",
         "area": "深圳", "industry": "银行", "market": "主板",
         "list_date": "19910403", "delist_date": None},
        {"ts_code": "000004.SZ", "symbol": "000004", "name": "国华网安",
         "area": "深圳", "industry": "软件", "market": "主板",
         "list_date": "19910114", "delist_date": "20260713"},
    ])
    df = normalize_stock_meta([raw])
    assert df.height == 2
    assert set(df.columns) >= {"ts_code", "code", "name", "list_date", "delist_date"}
    row = df.filter(pl.col("code") == "000004").row(named=True)
    assert row["list_date"] == date(1991, 1, 14)
    assert row["delist_date"] == date(2026, 7, 13)
    row0 = df.filter(pl.col("code") == "000001").row(named=True)
    assert row0["delist_date"] is None


def test_sync_stock_list_calls_l_and_d(tmp_path, monkeypatch):
    """R12：stock_basic 调用 L 与 D 两次并合并。"""
    import trendradar.infrastructure.tushare.stocklist as stocklist

    calls = []

    class FakePro:
        def stock_basic(self, exchange, list_status, fields):
            calls.append(list_status)
            return pd.DataFrame([{
                "ts_code": f"00000{1 if list_status == 'L' else 9}.SZ",
                "symbol": f"00000{1 if list_status == 'L' else 9}",
                "name": "X", "area": "", "industry": "", "market": "主板",
                "list_date": "20100101",
                "delist_date": None if list_status == "L" else "20260713",
            }])

    monkeypatch.setattr(stocklist, "get_pro", lambda: FakePro())
    bars_dir = tmp_path / "market" / "bars"
    df = stocklist.sync_stock_list(bars_dir)
    assert calls == ["L", "D"]
    assert df.height == 2
    assert "delist_date" in df.columns
    saved = pl.read_parquet(tmp_path / "market" / "stock_meta.parquet")
    assert saved.height == 2
