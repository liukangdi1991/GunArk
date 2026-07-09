from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import polars as pl

from web.core.config import ROOT


CACHE_PATH = ROOT / "storage" / "cache" / "trading_calendar.parquet"
DEFAULT_EXCHANGE = "SSE"
ProgressLog = Callable[..., None]

SAMPLE_CODES = (
    "000001",
    "000002",
    "000063",
    "000333",
    "000651",
    "000858",
    "600000",
    "600036",
    "600519",
    "601318",
)
FALLBACK_SAMPLE_SIZE = 24


@dataclass(frozen=True)
class CalendarDay:
    cal_date: dt.date
    is_open: bool
    pretrade_date: dt.date | None = None
    exchange: str = DEFAULT_EXCHANGE
    source: str = "tushare"


_lock = RLock()
_loaded = False
_days_by_date: dict[dt.date, CalendarDay] = {}
_source = "empty"


def load_calendar() -> None:
    """Load local trading calendar cache into memory. No network calls here."""
    global _loaded, _days_by_date, _source
    with _lock:
        if _loaded:
            return
        _days_by_date = _read_cache()
        _source = "cache" if _days_by_date else "empty"
        _loaded = True


def list_open_dates(
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> list[dt.date]:
    load_calendar()
    with _lock:
        dates = [
            day.cal_date
            for day in _days_by_date.values()
            if day.is_open
            and (start is None or day.cal_date >= start)
            and (end is None or day.cal_date <= end)
        ]
    return sorted(dates)


def latest_open_date_on_or_before(value: dt.date) -> dt.date | None:
    dates = list_open_dates(end=value)
    return dates[-1] if dates else None


def has_official_coverage(*, start: dt.date, end: dt.date) -> bool:
    load_calendar()
    if start > end:
        start, end = end, start
    with _lock:
        existing_dates = sorted(
            day.cal_date
            for day in _days_by_date.values()
            if day.source == "tushare"
        )
    return not _missing_year_ranges(start=start, end=end, existing_dates=existing_dates)


def coverage() -> dict[str, Any]:
    load_calendar()
    with _lock:
        rows = list(_days_by_date.values())
        source = _source
        sources = sorted({day.source for day in rows})
    if not rows:
        return {
            "cache_path": str(CACHE_PATH),
            "count": 0,
            "open_count": 0,
            "official_count": 0,
            "first_date": None,
            "last_date": None,
            "loaded": _loaded,
            "source": source,
            "sources": [],
        }
    dates = sorted(day.cal_date for day in rows)
    return {
        "cache_path": str(CACHE_PATH),
        "count": len(rows),
        "open_count": sum(1 for day in rows if day.is_open),
        "official_count": sum(1 for day in rows if day.source == "tushare"),
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "loaded": _loaded,
        "source": source,
        "sources": sources,
    }


def ensure_coverage(
    *,
    pro: Any,
    start: dt.date,
    end: dt.date,
    log: ProgressLog | None = None,
) -> dict[str, Any]:
    """Ensure local cache covers the requested range, refreshing from Tushare if needed."""
    load_calendar()
    if start > end:
        start, end = end, start

    with _lock:
        existing_dates = sorted(
            day.cal_date
            for day in _days_by_date.values()
            if day.source == "tushare"
        )

    missing_ranges = _missing_year_ranges(start=start, end=end, existing_dates=existing_dates)

    if not missing_ranges:
        return {"refreshed": False, **coverage()}

    refreshed = False
    try:
        for fetch_start, fetch_end in missing_ranges:
            if fetch_start <= fetch_end:
                refreshed = refresh_from_tushare(
                    pro=pro,
                    start=fetch_start,
                    end=fetch_end,
                    log=log,
                ) or refreshed
    except Exception as exc:  # noqa: BLE001 - calendar fallback should not block market sync.
        if log:
            log(f"交易日历刷新失败，继续使用本地缓存: {exc}", "WARN")
        return {"refreshed": False, "error": str(exc), **coverage()}

    return {"refreshed": refreshed, **coverage()}


def refresh_from_tushare(
    *,
    pro: Any,
    start: dt.date,
    end: dt.date,
    log: ProgressLog | None = None,
) -> bool:
    global _source
    if pro is None:
        raise RuntimeError("Tushare client 未初始化")
    if start > end:
        start, end = end, start

    if log:
        log(f"刷新交易日历: {start.isoformat()} ~ {end.isoformat()}")
    df = pro.trade_cal(
        exchange=DEFAULT_EXCHANGE,
        start_date=_format_ts_date(start),
        end_date=_format_ts_date(end),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    rows = _rows_from_tushare_df(df)
    if not rows:
        raise RuntimeError("Tushare trade_cal 未返回有效交易日历")

    with _lock:
        _days_by_date.update({row.cal_date: row for row in rows})
        _write_cache(_days_by_date)
        _source = "cache"
    if log:
        open_count = sum(1 for row in rows if row.is_open)
        log(f"交易日历已更新: {len(rows)} 天，其中交易日 {open_count} 天")
    return True


def load_fallback_from_market_data(data_dir: Path) -> dict[str, Any]:
    """Fallback for old environments without a Tushare calendar cache.

    This only seeds the in-memory calendar for UI/date selection. It does not
    persist, because persisted calendar files should come from Tushare trade_cal.
    """
    global _source
    load_calendar()
    if list_open_dates():
        return {"seeded": False, **coverage()}

    dates = _read_sample_market_dates(data_dir)
    if not dates:
        return {"seeded": False, **coverage()}

    rows = {
        day: CalendarDay(cal_date=day, is_open=True, pretrade_date=None, source="market_data")
        for day in sorted(dates)
    }
    with _lock:
        _days_by_date.update(rows)
        _source = "market_data_fallback"
    return {"seeded": True, **coverage()}


def seed_from_market_data(data_dir: Path) -> dict[str, Any]:
    return load_fallback_from_market_data(data_dir)


def _read_cache() -> dict[dt.date, CalendarDay]:
    if not CACHE_PATH.exists():
        return {}
    try:
        df = pl.read_parquet(CACHE_PATH)
    except Exception:
        return {}

    rows: dict[dt.date, CalendarDay] = {}
    columns = set(df.columns)
    default_source = _default_cache_source(df)
    for item in df.to_dicts():
        cal_date = _parse_any_date(item.get("cal_date"))
        if cal_date is None:
            continue
        rows[cal_date] = CalendarDay(
            exchange=str(item.get("exchange") or DEFAULT_EXCHANGE),
            cal_date=cal_date,
            is_open=_as_bool(item.get("is_open")),
            pretrade_date=_parse_any_date(item.get("pretrade_date")),
            source=str(item.get("source") or default_source) if "source" in columns else default_source,
        )
    return rows


def _write_cache(rows: dict[dt.date, CalendarDay]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "exchange": day.exchange,
            "cal_date": day.cal_date.isoformat(),
            "is_open": 1 if day.is_open else 0,
            "pretrade_date": day.pretrade_date.isoformat() if day.pretrade_date else None,
            "source": day.source,
        }
        for day in sorted(rows.values(), key=lambda item: item.cal_date)
        if day.source == "tushare"
    ]
    if data:
        pl.DataFrame(data).write_parquet(CACHE_PATH, compression="zstd")
    else:
        pl.DataFrame(
            {
                "exchange": [],
                "cal_date": [],
                "is_open": [],
                "pretrade_date": [],
                "source": [],
            }
        ).write_parquet(CACHE_PATH, compression="zstd")


def _missing_year_ranges(
    *,
    start: dt.date,
    end: dt.date,
    existing_dates: list[dt.date],
) -> list[tuple[dt.date, dt.date]]:
    ranges: list[tuple[dt.date, dt.date]] = []
    for year in range(start.year, end.year + 1):
        year_start = dt.date(year, 1, 1)
        year_end = dt.date(year, 12, 31)
        if _year_is_covered(existing_dates, year_start=year_start, year_end=year_end):
            continue
        ranges.append((year_start, year_end))
    return ranges


def _year_is_covered(
    existing_dates: list[dt.date],
    *,
    year_start: dt.date,
    year_end: dt.date,
) -> bool:
    if not existing_dates:
        return False
    return existing_dates[0] <= year_start and existing_dates[-1] >= year_end


def _rows_from_tushare_df(df: Any) -> list[CalendarDay]:
    if df is None or getattr(df, "empty", True):
        return []

    rows: list[CalendarDay] = []
    records = df.to_dict(orient="records")
    for item in records:
        cal_date = _parse_any_date(item.get("cal_date"))
        if cal_date is None:
            continue
        rows.append(
            CalendarDay(
                exchange=str(item.get("exchange") or DEFAULT_EXCHANGE),
                cal_date=cal_date,
                is_open=_as_bool(item.get("is_open")),
                pretrade_date=_parse_any_date(item.get("pretrade_date")),
                source="tushare",
            )
        )
    return rows


def _default_cache_source(df: pl.DataFrame) -> str:
    if "source" in df.columns:
        return "tushare"
    if "is_open" not in df.columns:
        return "legacy_open_dates_cache"
    try:
        has_closed_days = (df["is_open"].cast(pl.Int64, strict=False) == 0).any()
    except Exception:
        has_closed_days = False
    return "tushare" if has_closed_days else "legacy_open_dates_cache"


def _read_sample_market_dates(data_dir: Path) -> set[dt.date]:
    dates: set[dt.date] = set()
    for path in _sample_market_files(data_dir):
        try:
            values = pl.read_parquet(path, columns=["date"])["date"].unique().to_list()
        except Exception:
            continue
        for value in values:
            parsed = _parse_any_date(value)
            if parsed is not None:
                dates.add(parsed)
    return dates


def _sample_market_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []

    result: list[Path] = []
    seen: set[Path] = set()
    for code in SAMPLE_CODES:
        path = data_dir / f"{code}.parquet"
        if path.exists():
            result.append(path)
            seen.add(path)

    if len(result) >= FALLBACK_SAMPLE_SIZE:
        return result

    for path in sorted(data_dir.glob("*.parquet")):
        if path in seen:
            continue
        result.append(path)
        if len(result) >= FALLBACK_SAMPLE_SIZE:
            break
    return result


def _parse_any_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if "-" in text:
            return dt.datetime.strptime(text, "%Y-%m-%d").date()
        return dt.datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y"}


def _format_ts_date(value: dt.date) -> str:
    return value.strftime("%Y%m%d")
