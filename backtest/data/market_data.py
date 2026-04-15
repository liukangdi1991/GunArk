from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import polars as pl


class MarketDataProvider:
    def __init__(self, parquet_dir: Path, csv_dir: Path) -> None:
        self.parquet_dir = parquet_dir
        self.csv_dir = csv_dir
        self._cache: Dict[str, pd.DataFrame] = {}

    def preload(self, codes: Iterable[str]) -> None:
        for code in codes:
            self.load_code(code)

    def load_code(self, code: str) -> Optional[pd.DataFrame]:
        code = str(code).zfill(6)
        if code in self._cache:
            return self._cache[code]

        parquet_file = self.parquet_dir / f"{code}.parquet"
        csv_file = self.csv_dir / f"{code}.csv"
        df: Optional[pd.DataFrame] = None

        if parquet_file.exists():
            pl_df = pl.read_parquet(parquet_file).select(["date", "open", "close", "high", "low", "volume"])
            df = pd.DataFrame(pl_df.to_dict(as_series=False))
        elif csv_file.exists():
            df = pd.read_csv(csv_file, usecols=["date", "open", "close", "high", "low", "volume"])

        if df is None or df.empty:
            return None

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        self._cache[code] = df
        return df

    def get_calendar(self) -> List[date]:
        dates = set()
        for df in self._cache.values():
            dates.update(df["date"].tolist())
        return sorted(dates)

    def get_row(self, code: str, dt: date) -> Optional[pd.Series]:
        df = self.load_code(code)
        if df is None:
            return None
        matched = df[df["date"] == dt]
        if matched.empty:
            return None
        return matched.iloc[-1]

    def get_latest_close(self, code: str, dt: date, fallback_price: float) -> float:
        df = self.load_code(code)
        if df is None:
            return fallback_price
        hist = df[df["date"] <= dt]
        if hist.empty:
            return fallback_price
        return float(hist.iloc[-1]["close"])
