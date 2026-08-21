"""Tests for MarketDataStore implementations."""

import polars as pl
from datetime import date, timedelta
from pathlib import Path

from trendradar.domain.market.data_store import (
    LocalParquetMarketStore,
)


def _make_bars_file(path: Path, code: str, dates: list[date]) -> None:
    """Create a test parquet file with synthetic bar data."""
    n = len(dates)
    df = pl.DataFrame(
        {
            "date": dates,
            "open": [10.0 + i * 0.1 for i in range(n)],
            "high": [11.0 + i * 0.1 for i in range(n)],
            "low": [9.0 + i * 0.1 for i in range(n)],
            "close": [10.5 + i * 0.1 for i in range(n)],
            "volume": [1000.0 + i * 100 for i in range(n)],
            "amount": [10000.0 + i * 1000 for i in range(n)],
            "adj_factor": [1.0] * n,
            "is_suspended": [False] * n,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def test_load_bars_returns_dataframe_for_existing_codes(tmp_path):
    bars_dir = tmp_path / "bars"
    dates = [date(2024, 1, d) for d in range(1, 11)]
    _make_bars_file(bars_dir / "000001.parquet", "000001", dates)

    store = LocalParquetMarketStore(bars_dir)
    result = store.load_bars(
        ["000001"], start=date(2024, 1, 1), end=date(2024, 12, 31)
    )

    assert isinstance(result, pl.DataFrame)
    assert not result.is_empty()
    assert "code" in result.columns
    assert result["code"].unique().to_list() == ["000001"]


def test_load_bars_filters_by_date_range(tmp_path):
    bars_dir = tmp_path / "bars"
    dates = [date(2024, 1, d) for d in range(1, 11)]
    _make_bars_file(bars_dir / "000001.parquet", "000001", dates)

    store = LocalParquetMarketStore(bars_dir)
    result = store.load_bars(
        ["000001"], start=date(2024, 1, 5), end=date(2024, 1, 7)
    )

    result_dates = result["date"].unique().sort().to_list()
    assert result_dates == [date(2024, 1, 5), date(2024, 1, 6), date(2024, 1, 7)]


def test_load_bars_returns_empty_for_nonexistent_code(tmp_path):
    bars_dir = tmp_path / "bars"

    store = LocalParquetMarketStore(bars_dir)
    result = store.load_bars(
        ["999999"], start=date(2024, 1, 1), end=date(2024, 12, 31)
    )

    assert result.is_empty()


def test_load_bars_handles_mixed_codes(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, d) for d in range(1, 6)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, d) for d in range(1, 6)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.load_bars(
        ["000001", "000002", "999999"],
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
    )

    codes = result["code"].unique().sort().to_list()
    assert codes == ["000001", "000002"]


def test_load_bars_respects_columns_filter(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, d) for d in range(1, 6)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.load_bars(
        ["000001"],
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        columns=["code", "date", "close"],
    )

    assert result.columns == ["code", "date", "close"]


def test_latest_trade_date_returns_max_date(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 15)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 8), date(2024, 1, 20)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.latest_trade_date()
    assert result == date(2024, 1, 20)


def test_latest_trade_date_returns_none_for_empty_dir(tmp_path):
    bars_dir = tmp_path / "bars"
    store = LocalParquetMarketStore(bars_dir)
    assert store.latest_trade_date() is None


def test_trading_dates_returns_sorted_unique_dates(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 15)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 8), date(2024, 1, 10), date(2024, 1, 20)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.trading_dates(
        start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    assert result == [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 10),
        date(2024, 1, 15),
        date(2024, 1, 20),
    ]


def test_trading_dates_filters_by_range(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 15)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.trading_dates(
        start=date(2024, 1, 8), end=date(2024, 1, 12)
    )
    assert result == [date(2024, 1, 10)]


def test_get_calendar_returns_sorted_all_dates(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 8)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.get_calendar()
    assert result == [date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 10)]


def test_get_calendar_empty_dir(tmp_path):
    bars_dir = tmp_path / "bars"
    store = LocalParquetMarketStore(bars_dir)
    assert store.get_calendar() == []


def test_get_calendar_refreshes_when_bars_change(tmp_path):
    """Regression: the calendar.parquet cache must not outlive new bar data."""
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 8)],
    )
    store = LocalParquetMarketStore(bars_dir)

    first = store.get_calendar()
    assert first == [date(2024, 1, 5), date(2024, 1, 8)]
    assert (bars_dir.parent / "calendar.parquet").exists()

    # New bar file with later dates arrives (e.g. after a market sync).
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 10), date(2024, 1, 15)],
    )
    # A real sync happens minutes after the previous one; bump mtime so the
    # cache invalidation sees the new file (same-second writes would hide it).
    import os
    import time

    new_mtime = time.time() + 5
    os.utime(bars_dir / "000002.parquet", (new_mtime, new_mtime))

    refreshed = store.get_calendar()
    assert refreshed == [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 10),
        date(2024, 1, 15),
    ]


def test_get_row_returns_single_row_dict(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10)],
    )

    store = LocalParquetMarketStore(bars_dir)
    row = store.get_row("000001", date(2024, 1, 5))
    assert row is not None
    assert row["code"] == "000001"
    assert row["date"] == date(2024, 1, 5)
    assert row["close"] == 10.5


def test_get_row_returns_none_for_missing_date(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5)],
    )

    store = LocalParquetMarketStore(bars_dir)
    row = store.get_row("000001", date(2024, 1, 6))
    assert row is None


def test_get_row_returns_none_for_missing_code(tmp_path):
    bars_dir = tmp_path / "bars"

    store = LocalParquetMarketStore(bars_dir)
    row = store.get_row("999999", date(2024, 1, 5))
    assert row is None


def test_get_previous_close_finds_prior_close(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 15)],
    )

    store = LocalParquetMarketStore(bars_dir)
    prev = store.get_previous_close("000001", date(2024, 1, 12))
    assert prev == 10.6  # close for 2024-01-10: 10.5 + 1 * 0.1


def test_get_previous_close_returns_none_if_no_prior_date(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5)],
    )

    store = LocalParquetMarketStore(bars_dir)
    prev = store.get_previous_close("000001", date(2024, 1, 4))
    assert prev is None


def test_get_rows_returns_date_filtered_rows(tmp_path):
    bars_dir = tmp_path / "bars"
    dates = [date(2024, 1, d) for d in range(1, 11)]
    _make_bars_file(bars_dir / "000001.parquet", "000001", dates)

    store = LocalParquetMarketStore(bars_dir)
    result = store.get_rows(
        "000001", start_date=date(2024, 1, 3), end_date=date(2024, 1, 7)
    )

    assert isinstance(result, pl.DataFrame)
    result_dates = result["date"].unique().sort().to_list()
    assert result_dates == [
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 6),
        date(2024, 1, 7),
    ]


def test_get_rows_empty_for_missing_code(tmp_path):
    bars_dir = tmp_path / "bars"

    store = LocalParquetMarketStore(bars_dir)
    result = store.get_rows(
        "999999", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
    )
    assert result.is_empty()


def test_stock_meta_returns_dataframe(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 5)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.stock_meta()
    assert isinstance(result, pl.DataFrame)
    codes = result["code"].sort().to_list()
    assert codes == ["000001", "000002"]


def test_stock_meta_with_code_filter(tmp_path):
    bars_dir = tmp_path / "bars"
    _make_bars_file(
        bars_dir / "000001.parquet", "000001",
        [date(2024, 1, 5)],
    )
    _make_bars_file(
        bars_dir / "000002.parquet", "000002",
        [date(2024, 1, 5)],
    )

    store = LocalParquetMarketStore(bars_dir)
    result = store.stock_meta(codes=["000001"])
    codes = result["code"].to_list()
    assert codes == ["000001"]
