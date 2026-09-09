from datetime import date

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.stocklist import (
    build_effective_list,
    normalize_stock_meta,
)

META = pl.DataFrame({
    "ts_code": ["000001.SZ", "000004.SZ", "920099.BJ", "833171.BJ", "300001.SZ", "688001.SH", "301999.SZ"],
    "code": ["000001", "000004", "920099", "833171", "300001", "688001", "301999"],
    "name": ["平安银行", "国华网安", "瑞华技术", "转板样本", "特锐德", "华兴源创", "未来上市"],
    "list_date": [date(1991, 4, 3), date(1991, 1, 14), date(2020, 7, 27),
                  date(2020, 7, 27), date(2009, 10, 30), date(2019, 7, 22), date(2027, 1, 1)],
    "delist_date": [None, date(2026, 7, 13), None, date(2022, 5, 25), None, None, None],
})

LATEST = date(2026, 8, 27)


def test_effective_list_includes_bse_codes():
    """R24：920x 与转板 833x/832x 精选层段均进 effective（不再按 .BJ 剔除）。"""
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert "920099" in eff.codes
    assert "301999" not in eff.codes          # list_date > latest 仍剔
    assert set(eff.codes) == {"000001", "000004", "920099", "833171", "300001", "688001"}


def test_effective_list_exclude_boards_gem_star():
    eff = build_effective_list(META, exclude_boards=["gem", "star"], latest_tradeable=LATEST)
    assert set(eff.codes) == {"000001", "000004", "920099", "833171"}   # exclude_boards 无 bse 键，BJ 回归


def test_effective_clamped_range():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert eff.clamped_range("000001") == (date(2015, 1, 1), LATEST)      # BASELINE 钳制
    assert eff.clamped_range("000004") == (date(2015, 1, 1), date(2026, 7, 13))  # 退市钳制
    assert eff.clamped_range("920099") == (date(2020, 7, 27), LATEST)     # list_date 钳制
    assert eff.clamped_range("833171") == (date(2020, 7, 27), date(2022, 5, 25))  # 转板日钳制（精选层段）


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

def test_normalize_meta_nan_missing_values_infer_as_string():
    """缺失值以 float nan 混在字符串列里时不得让 polars 推断炸掉。

    生产事故（2026-09-09 全量重建）：D 帧 area 列开头连续 nan，polars 按
    列首样本把整列推断成 Float64，撞上后续 "吉林" 直接 TypeError。缺失值
    必须先归一为 None，让推断稳定落在 Utf8。
    """
    nan = float("nan")
    raw = pd.DataFrame({
        "ts_code": ["000003.SZ", "000024.SZ"],
        "symbol": ["000003", "000024"],
        "name": ["PT金田A(退)", "XX退"],
        "area": [nan, "吉林"],
        "industry": [nan, nan],
        "market": ["主板", "主板"],
        "list_date": ["19910703", "19920101"],
        "delist_date": ["20020614", None],
    })
    df = normalize_stock_meta([raw])
    assert df.height == 2
    assert df.schema["area"] == pl.Utf8
    assert df.filter(pl.col("code") == "000024").row(named=True)["area"] == "吉林"
    assert df.filter(pl.col("code") == "000003").row(named=True)["area"] is None
    assert df.schema["delist_date"] == pl.Date


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
