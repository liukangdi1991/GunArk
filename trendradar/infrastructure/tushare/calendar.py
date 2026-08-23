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
    """Fetch trade days in [start, end] from Tushare (exchange SSE).

    Sharded by calendar year so any range stays under the per-call row
    ceiling (6000 rows; a multi-decade range is ~9000+ trade days).
    """
    frames = []
    year = start.year
    while year <= end.year:
        seg_start = max(start, date(year, 1, 1))
        seg_end = min(end, date(year, 12, 31))
        resp = pro.trade_cal(
            exchange="SSE",
            start_date=seg_start.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
        )
        if resp is not None and resp.to_dict(orient="list"):
            # pl.DataFrame(dict-of-lists) avoids pl.from_pandas, which can
            # require pyarrow for some object columns (e.g. sparse early-year
            # trade_cal responses).
            frames.append(pl.DataFrame(resp.to_dict(orient="list")))
        year += 1

    if not frames:
        return []
    df = pl.concat(frames)
    return sorted(
        df.filter(pl.col("is_open") == 1)
        .select(
            pl.col("cal_date")
            .cast(pl.Utf8)
            .str.strptime(pl.Date, "%Y%m%d")
            .alias("date")
        )["date"]
        .to_list()
    )
