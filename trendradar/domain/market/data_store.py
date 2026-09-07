"""Market data store interface and local parquet implementation."""

from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional

import polars as pl

_calendar_lock = __import__("threading").Lock()


def _atomic_write_calendar_cache(cache_path: Path, dates: list[date]) -> None:
    """临时文件 + 换名落盘：原地写会截断共享同一 inode 的副本，中断时还留半截文件。"""
    fd, tmp = tempfile.mkstemp(dir=cache_path.parent, suffix=".tmp")
    try:
        os.close(fd)
        pl.DataFrame({"date": dates}).with_columns(
            pl.col("date").cast(pl.Date)
        ).write_parquet(tmp)
        os.replace(tmp, cache_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


POLARS_KLINE_SCHEMA: dict[str, type] = {
    "code": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "pre_close": pl.Float64,
    "adj_factor": pl.Float64,
    "is_suspended": pl.Boolean,
}


class MarketDataStore(ABC):
    """Abstract interface for market data access."""

    @abstractmethod
    def load_bars(
        self,
        codes: list[str],
        start: date,
        end: date,
        columns: Optional[list[str]] = None,
    ) -> pl.DataFrame: ...

    @abstractmethod
    def latest_trade_date(self) -> Optional[date]: ...

    @abstractmethod
    def trading_dates(self, start: date, end: date) -> list[date]: ...

    @abstractmethod
    def stock_meta(self, codes: Optional[list[str]] = None) -> pl.DataFrame: ...

    @abstractmethod
    def get_calendar(self) -> list[date]: ...

    @abstractmethod
    def get_row(self, code: str, dt: date) -> Optional[dict]: ...

    @abstractmethod
    def get_previous_close(self, code: str, dt: date) -> Optional[float]: ...

    @abstractmethod
    def get_rows(
        self, code: str, start_date: date, end_date: date
    ) -> pl.DataFrame: ...


class LocalParquetMarketStore(MarketDataStore):
    """Market data store backed by per-stock parquet files.

    Reads from ``<bars_dir>/{code}.parquet`` files.
    """

    def __init__(self, bars_dir: Path) -> None:
        self.bars_dir = Path(bars_dir)

    # ------------------------------------------------------------------
    # load_bars
    # ------------------------------------------------------------------

    def load_bars(
        self,
        codes: list[str],
        start: date,
        end: date,
        columns: Optional[list[str]] = None,
    ) -> pl.DataFrame:
        paths = [self.bars_dir / f"{c}.parquet" for c in codes]
        existing = [p for p in paths if p.exists()]
        if not existing:
            return pl.DataFrame(schema=POLARS_KLINE_SCHEMA)

        dfs: list[pl.DataFrame] = []
        for p in existing:
            df = pl.read_parquet(p)
            code = p.stem
            df = df.with_columns(pl.lit(code).cast(pl.Utf8).alias("code"))
            dfs.append(df)

        result = pl.concat(dfs, how="diagonal").filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        )
        if columns:
            result = result.select(columns)
        return result

    # ------------------------------------------------------------------
    # latest_trade_date
    # ------------------------------------------------------------------

    def latest_trade_date(self) -> Optional[date]:
        dates = self._collect_dates()
        if not dates:
            return None
        return dates[-1]

    # ------------------------------------------------------------------
    # trading_dates
    # ------------------------------------------------------------------

    def trading_dates(self, start: date, end: date) -> list[date]:
        dates = self._collect_dates()
        return [d for d in dates if start <= d <= end]

    # ------------------------------------------------------------------
    # get_calendar
    # ------------------------------------------------------------------

    def get_calendar(self) -> list[date]:
        return self._collect_dates()

    # ------------------------------------------------------------------
    # get_row
    # ------------------------------------------------------------------

    def get_row(self, code: str, dt: date) -> Optional[dict]:
        path = self.bars_dir / f"{code}.parquet"
        if not path.exists():
            return None
        df = pl.read_parquet(path)
        df = df.with_columns(pl.lit(code).cast(pl.Utf8).alias("code"))
        mask = df.filter(pl.col("date") == dt)
        if mask.is_empty():
            return None
        return mask.row(0, named=True)

    # ------------------------------------------------------------------
    # get_previous_close
    # ------------------------------------------------------------------

    def get_previous_close(self, code: str, dt: date) -> Optional[float]:
        path = self.bars_dir / f"{code}.parquet"
        if not path.exists():
            return None
        df = pl.read_parquet(path)
        prior = df.filter(pl.col("date") < dt).sort("date", descending=True)
        if prior.is_empty():
            return None
        return prior.row(0, named=True)["close"]

    # ------------------------------------------------------------------
    # get_rows
    # ------------------------------------------------------------------

    def get_rows(
        self, code: str, start_date: date, end_date: date
    ) -> pl.DataFrame:
        path = self.bars_dir / f"{code}.parquet"
        if not path.exists():
            return pl.DataFrame(schema=POLARS_KLINE_SCHEMA)
        df = pl.read_parquet(path)
        df = df.with_columns(pl.lit(code).cast(pl.Utf8).alias("code"))
        return df.filter(
            (pl.col("date") >= start_date) & (pl.col("date") <= end_date)
        )

    # ------------------------------------------------------------------
    # stock_meta
    # ------------------------------------------------------------------

    def stock_meta(self, codes: Optional[list[str]] = None) -> pl.DataFrame:
        meta_path = self.bars_dir.parent / "stock_meta.parquet"
        if meta_path.exists():
            try:
                df = pl.read_parquet(meta_path)
                if codes:
                    df = df.filter(pl.col("code").is_in(codes))
                return df
            except Exception:
                pass

        all_paths = sorted(self.bars_dir.glob("*.parquet"))
        metas = []
        for p in all_paths:
            code = p.stem
            if codes and code not in codes:
                continue
            metas.append({"code": code})
        if not metas:
            return pl.DataFrame(schema={"code": pl.Utf8})
        return pl.DataFrame(metas)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _collect_dates(self) -> list[date]:
        """Scan all parquet files and return sorted unique dates.

        Results are cached in ``calendar.parquet`` in the parent directory.
        The cache is invalidated when any bar file is newer than it (e.g.
        after a market sync adds or extends history).
        """
        cache_path = self.bars_dir.parent / "calendar.parquet"
        try:
            if cache_path.exists():
                newest_bar = 0.0
                for p in self.bars_dir.glob("*.parquet"):
                    m = p.stat().st_mtime
                    if m > newest_bar:
                        newest_bar = m
                if newest_bar <= cache_path.stat().st_mtime:
                    cached = pl.read_parquet(cache_path)
                    if not cached.is_empty():
                        return sorted(cached["date"].unique().to_list())
        except Exception:
            pass

        with _calendar_lock:
            all_paths = sorted(self.bars_dir.glob("*.parquet"))
            if not all_paths:
                return []

            try:
                dates = (
                    pl.scan_parquet([str(p) for p in all_paths], columns=["date"])
                    .select(pl.col("date").unique())
                    .collect()["date"]
                    .to_list()
                )
            except Exception:
                date_sets: list[set[date]] = []
                for p in all_paths:
                    try:
                        df = pl.read_parquet(p, columns=["date"])
                        date_sets.append(set(df["date"].unique().to_list()))
                    except Exception:
                        continue
                if not date_sets:
                    return []
                dates = sorted(date_sets[0].union(*date_sets[1:]))

            result = sorted(dates)

            try:
                _atomic_write_calendar_cache(cache_path, result)
            except Exception:
                pass

            return result
