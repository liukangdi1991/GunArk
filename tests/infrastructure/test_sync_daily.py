from datetime import date
import polars as pl
from pathlib import Path
import tempfile

from trendradar.infrastructure.tushare.syncer import merge_day_bars


def _day_df(rows):
    return pl.DataFrame(rows)


def test_merge_day_creates_new_code(tmp_path):
    bars_dir = tmp_path / "bars"
    df = _day_df([
        {"code": "000001", "date": date(2026, 8, 20), "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1000.0, "amount": 10400.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(df, bars_dir)
    assert (bars_dir / "000001.parquet").exists()
    out = pl.read_parquet(bars_dir / "000001.parquet")
    assert out.height == 1
    assert out["date"][0] == date(2026, 8, 20)


def test_merge_day_appends_and_dedups_new_wins(tmp_path):
    bars_dir = tmp_path / "bars"
    first = _day_df([
        {"code": "000001", "date": date(2026, 8, 19), "open": 9.0, "high": 9.5,
         "low": 8.8, "close": 9.2, "volume": 1000.0, "amount": 9200.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(first, bars_dir)
    # Same date with new close -> new row wins; plus a new day
    second = _day_df([
        {"code": "000001", "date": date(2026, 8, 19), "open": 9.0, "high": 9.5,
         "low": 8.8, "close": 9.9, "volume": 1000.0, "amount": 9200.0,
         "adj_factor": 1.0, "is_suspended": False},
        {"code": "000001", "date": date(2026, 8, 20), "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1000.0, "amount": 10400.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(second, bars_dir)
    out = pl.read_parquet(bars_dir / "000001.parquet")
    assert out.height == 2
    assert sorted(out["date"].to_list()) == [date(2026, 8, 19), date(2026, 8, 20)]
    by_date = {r["date"]: r for r in out.to_dicts()}
    assert by_date[date(2026, 8, 19)]["close"] == 9.9  # new wins
