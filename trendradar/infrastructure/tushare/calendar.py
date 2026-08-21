"""Authoritative trading calendar (Tushare trade_cal) with local cache.

Used for sync decisions (latest tradeable day, missing-day computation).
Distinct from storage/market/calendar.parquet (bar-data-driven, used by
selection/backtest reads).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def save_trade_calendar(path: Path, dates: list[date]) -> None:
    """Atomically persist the trade calendar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = __import__("tempfile").mkstemp(dir=path.parent, suffix=".tmp")
    try:
        __import__("os").close(fd)
        pl.DataFrame({"date": dates}).with_columns(
            pl.col("date").cast(pl.Date)
        ).write_parquet(tmp)
        __import__("os").replace(tmp, path)
    except BaseException:
        try:
            __import__("os").unlink(tmp)
        except OSError:
            pass
        raise


def load_trade_calendar(path: Path) -> list[date] | None:
    if not path.exists():
        return None
    try:
        df = pl.read_parquet(path)
        if df.is_empty():
            return None
        return sorted(df["date"].unique().to_list())
    except Exception:
        return None


def fetch_trade_calendar(pro, start: date, end: date) -> list[date]:
    """Fetch trade days in [start, end] from Tushare (exchange SSE)."""
    resp = pro.trade_cal(
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if resp is None or resp.empty:
        return []
    return sorted(
        pl.from_pandas(resp)
        .filter(pl.col("is_open") == 1)
        .select(
            pl.col("cal_date")
            .cast(pl.Utf8)
            .str.strptime(pl.Date, "%Y%m%d")
            .alias("date")
        )["date"]
        .to_list()
    )
