"""Tushare daily kline syncer with rate limiting, retry, and atomic write."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.client import get_pro

logger = logging.getLogger(__name__)

IP_BAN_ERROR_MSG = "每分钟最多访问该接口"


def sync_kline(
    codes: list[str],
    start: date,
    end: date,
    bars_dir: Path,
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Sync daily kline for given codes to parquet files.

    Returns:
        dict with keys ``synced``, ``skipped``, ``failed``, ``empty``.
    """
    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)

    pro = get_pro()
    results = {"synced": 0, "skipped": 0, "failed": 0, "empty": 0}
    total = len(codes)
    max_retries = 3

    for idx, code in enumerate(codes, start=1):
        if cancel_check and cancel_check():
            break

        if progress:
            progress(idx, total, code)

        output_path = bars_dir / f"{code}.parquet"

        if _is_up_to_date(output_path, end):
            results["skipped"] += 1
            continue

        data = _fetch_with_retry(pro, code, start, end, max_retries)
        if data is None:
            results["failed"] += 1
            continue

        if data.is_empty():
            results["empty"] += 1
            continue

        _atomic_write_parquet(data, output_path)
        results["synced"] += 1

    return results


def _is_up_to_date(path: Path, target_end: date) -> bool:
    if not path.exists():
        return False
    try:
        df = pl.read_parquet(path, columns=["date"])
        if df.is_empty():
            return False
        latest = df["date"].max()
        if latest is None:
            return False
        return latest >= target_end
    except Exception:
        return False


def _fetch_with_retry(
    pro, code: str, start: date, end: date, max_retries: int
) -> Optional[pl.DataFrame]:
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    params = {
        "ts_code": code,
        "start_date": start_s,
        "end_date": end_s,
        "freq": "D",
    }

    for attempt in range(max_retries):
        try:
            resp = pro.daily(**params)
            return _response_to_df(resp, code)
        except Exception as e:
            msg = str(e)
            if IP_BAN_ERROR_MSG in msg:
                logger.warning(
                    "Rate limit hit on %s attempt %d/%d, cooling 600s",
                    code, attempt + 1, max_retries,
                )
                time.sleep(600)
                continue
            backoff = 2 ** attempt
            logger.warning(
                "Fetch error for %s attempt %d/%d: %s, retry in %ds",
                code, attempt + 1, max_retries, msg, backoff,
            )
            time.sleep(backoff)

    logger.error("All %d retries exhausted for %s", max_retries, code)
    return None


def _response_to_df(resp: pd.DataFrame, code: str) -> pl.DataFrame:
    if resp is None or resp.empty:
        return pl.DataFrame()

    df = pl.from_pandas(resp)

    column_map = {
        "trade_date": "date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
        "amount": "amount",
    }
    existing_mappings = {k: v for k, v in column_map.items() if k in df.columns}
    df = df.rename(existing_mappings)

    if "date" in df.columns:
        df = df.with_columns(
            pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
        )

    for col_name in ["open", "high", "low", "close", "volume", "amount"]:
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Float64))

    df = df.with_columns(
        pl.lit(code).cast(pl.Utf8).alias("code"),
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
    )

    cols = ["code", "date", "open", "high", "low", "close", "volume", "amount", "adj_factor", "is_suspended"]
    existing = [c for c in cols if c in df.columns]
    df = df.select(existing)
    return df.sort("date")


def _atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.close(fd)
        df.write_parquet(tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
