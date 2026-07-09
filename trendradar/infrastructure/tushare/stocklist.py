"""Tushare stock list sync."""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from trendradar.infrastructure.tushare.client import get_pro

logger = logging.getLogger(__name__)


def sync_stock_list(bars_dir: Path) -> pl.DataFrame:
    """Fetch full stock list from Tushare, save to stock_meta.parquet."""
    pro = get_pro()

    data = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )

    if data is None or data.empty:
        logger.warning("stock_basic returned empty")
        return pl.DataFrame()

    df = pl.from_pandas(data)

    column_map = {
        "ts_code": "code",
        "symbol": "symbol",
        "name": "name",
        "area": "area",
        "industry": "industry",
        "market": "market",
        "list_date": "list_date",
    }
    existing = {k: v for k, v in column_map.items() if k in df.columns}
    df = df.rename(existing)

    if "list_date" in df.columns:
        df = df.with_columns(
            pl.col("list_date")
            .cast(pl.Utf8)
            .str.strptime(pl.Date, "%Y%m%d")
            .alias("list_date")
        )

    output = (Path(bars_dir).parent / "stock_meta.parquet")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output)

    return df
