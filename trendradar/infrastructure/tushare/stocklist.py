"""Tushare 股票清单：L ∪ D 两次调用 + delist_date + 有效清单。见 spec §5/§3.6。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

import polars as pl

from trendradar.domain.market.sync.spec import BASELINE_START
from trendradar.infrastructure.tushare.client import get_pro
from trendradar.infrastructure.tushare.fetch import EXCLUDE_BOARD_PREFIXES
from trendradar.infrastructure.tushare.writer import atomic_write_parquet

logger = logging.getLogger(__name__)

_FIELDS = "ts_code,symbol,name,area,industry,market,list_date,delist_date"


def normalize_stock_meta(frames: list) -> pl.DataFrame:
    """合并 L/D 两次响应为统一 schema（code/ts_code/list_date/delist_date…）。"""
    dfs = []
    for data in frames:
        if data is None or not data.to_dict(orient="list"):
            continue
        f = pl.DataFrame(data.to_dict(orient="list"))
        # L 帧的 delist_date 全空会推断成 Null 类型，与 D 帧的 Utf8 无法直接
        # concat —— 统一先转字符串，日期解析放到合并之后
        for col in ("list_date", "delist_date"):
            if col in f.columns:
                f = f.with_columns(pl.col(col).cast(pl.Utf8, strict=False))
        dfs.append(f)
    if not dfs:
        return pl.DataFrame()
    df = pl.concat(dfs)
    if "symbol" in df.columns:
        df = df.rename({"symbol": "code"})
    for col in ("list_date", "delist_date"):
        if col in df.columns:
            df = df.with_columns(
                pl.col(col).str.strptime(pl.Date, "%Y%m%d", strict=False)
                .alias(col)
            )
    return df.unique(subset=["code"], keep="first")


def sync_stock_list(bars_dir: Path) -> pl.DataFrame:
    """stock_basic(L) + stock_basic(D) 两次调用，写 stock_meta.parquet（原子）。"""
    pro = get_pro()
    frames = [
        pro.stock_basic(exchange="", list_status=s, fields=_FIELDS)
        for s in ("L", "D")
    ]
    df = normalize_stock_meta(frames)
    if df.is_empty():
        logger.warning("stock_basic returned empty for both L and D")
        return df

    output = Path(bars_dir).parent / "stock_meta.parquet"
    atomic_write_parquet(df, output)
    return df


@dataclass(frozen=True)
class EffectiveList:
    """有效清单 = L∪D − exclude_boards（与拉取侧同一过滤，§3.8 断言①分母）。"""

    codes: tuple[str, ...]
    rows: tuple[tuple[date, date | None], ...]  # (list_date, delist_date)
    _clamped: Mapping[str, tuple[date, date]]

    def clamped_range(self, code: str) -> tuple[date, date] | None:
        return self._clamped.get(code)


def build_effective_list(
    meta: pl.DataFrame,
    exclude_boards,
    latest_tradeable: date,
    baseline_start: date = BASELINE_START,
) -> EffectiveList:
    """spec §3.6 待拉清单 ①-④：剔未来上市 / 剔排除板块 / 区间钳制（北交所随全市场拉取）。"""
    if meta.is_empty() or latest_tradeable is None:
        return EffectiveList((), (), {})

    prefixes = tuple(
        p for b in (exclude_boards or []) for p in EXCLUDE_BOARD_PREFIXES.get(b, ())
    )
    codes: list[str] = []
    rows: list[tuple[date, date | None]] = []
    clamped: dict[str, tuple[date, date]] = {}

    for row in meta.iter_rows(named=True):
        code = str(row["code"])
        if prefixes and code.startswith(prefixes):
            continue
        list_d = row.get("list_date")
        if list_d is None or list_d > latest_tradeable:
            continue
        delist_d = row.get("delist_date")
        start = max(baseline_start, list_d)
        end = min(latest_tradeable, delist_d) if delist_d is not None else latest_tradeable
        if start > end:
            continue
        codes.append(code)
        rows.append((list_d, delist_d))
        clamped[code] = (start, end)

    return EffectiveList(tuple(codes), tuple(rows), clamped)
