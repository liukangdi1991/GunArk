"""官方交易日历拉取（Tushare trade_cal）。

本地存储为 SQLite trade_calendar 表（SyncStore.insert_calendar_days，
INSERT OR IGNORE 只增不减，spec §3.3）；parquet 缓存方案已废弃。
"""

from __future__ import annotations

from datetime import date

import polars as pl


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
