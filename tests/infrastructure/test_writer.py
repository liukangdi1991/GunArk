from datetime import date

import polars as pl

from trendradar.infrastructure.tushare.writer import (
    atomic_write_parquet,
    flush_by_code,
    readback_calendar,
    swap_in_bars,
)


def _rows(code, days, close0=10.0):
    return [
        {"code": code, "date": d, "open": close0, "high": close0 + 1,
         "low": close0 - 1, "close": close0, "volume": 100.0, "amount": 1000.0,
         "adj_factor": 1.0, "is_suspended": False}
        for d in days
    ]


def test_flush_by_code_creates_and_upserts(tmp_path):
    bars = tmp_path / "bars"
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), bars)
    # R19：重拉同日覆盖，历史行不变
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)], close0=12.0)
                               + _rows("000001", [date(2026, 8, 27)])), bars)
    out = pl.read_parquet(bars / "000001.parquet")
    assert out.height == 2
    by_date = {r["date"]: r for r in out.to_dicts()}
    assert by_date[date(2026, 8, 26)]["close"] == 12.0  # 同日新行覆盖
    assert by_date[date(2026, 8, 27)]["close"] == 10.0


def test_flush_by_code_multiple_codes(tmp_path):
    bars = tmp_path / "bars"
    df = pl.DataFrame(_rows("000001", [date(2026, 8, 27)]) + _rows("000002", [date(2026, 8, 27)]))
    written = flush_by_code(df, bars)
    assert sorted(written) == ["000001", "000002"]


def test_readback_calendar(tmp_path):
    bars = tmp_path / "bars"
    assert readback_calendar(bars) == set()
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26), date(2026, 8, 27)])), bars)
    assert readback_calendar(bars) == {date(2026, 8, 26), date(2026, 8, 27)}


def _setup_dirs(market):
    bars = market / "bars"
    staging = market / "staging"
    bars.mkdir(parents=True)
    staging.mkdir(parents=True)
    return bars, staging


def test_swap_one_way_rename(tmp_path):
    market = tmp_path / "market"
    bars, staging = _setup_dirs(market)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), bars)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 27)])), staging)

    swap_in_bars(market)

    # 新数据进 bars，上一版进 bars_prev，staging 重建为空
    out = pl.read_parquet(market / "bars" / "000001.parquet")
    assert out["date"].to_list() == [date(2026, 8, 27)]
    prev = pl.read_parquet(market / "bars_prev" / "000001.parquet")
    assert prev["date"].to_list() == [date(2026, 8, 26)]
    assert (market / "staging").is_dir()
    assert list((market / "staging").glob("*.parquet")) == []


def test_swap_second_round_overwrites_bars_prev(tmp_path):
    # R20：第二次全量重建不得被上一版残留"续传"；bars_prev 只留最近一版
    market = tmp_path / "market"
    bars, staging = _setup_dirs(market)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 25)])), bars)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), staging)
    swap_in_bars(market)

    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 27)])), market / "staging")
    swap_in_bars(market)

    out = pl.read_parquet(market / "bars" / "000001.parquet")
    assert out["date"].to_list() == [date(2026, 8, 27)]
    prev = pl.read_parquet(market / "bars_prev" / "000001.parquet")
    assert prev["date"].to_list() == [date(2026, 8, 26)]  # 上一版被覆盖，非 08-25
    assert list((market / "staging").glob("*.parquet")) == []


def test_swap_falls_back_without_renameat2(tmp_path, monkeypatch):
    """非 x86_64 Linux 走两步 rename 回退，结果必须与原子交换一致。"""
    from trendradar.infrastructure.tushare import writer

    monkeypatch.setattr(writer, "_CAN_EXCHANGE", False)
    market = tmp_path / "market"
    bars, staging = _setup_dirs(market)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), bars)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 27)])), staging)

    swap_in_bars(market)

    out = pl.read_parquet(market / "bars" / "000001.parquet")
    assert out["date"].to_list() == [date(2026, 8, 27)]
    prev = pl.read_parquet(market / "bars_prev" / "000001.parquet")
    assert prev["date"].to_list() == [date(2026, 8, 26)]
    assert list((market / "staging").glob("*.parquet")) == []


def test_atomic_write_no_partial(tmp_path):
    target = tmp_path / "a.parquet"
    df = pl.DataFrame({"x": [1]})
    atomic_write_parquet(df, target)
    assert pl.read_parquet(target)["x"].to_list() == [1]
