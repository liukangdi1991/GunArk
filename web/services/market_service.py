from __future__ import annotations

import csv
import datetime as dt
import errno
import os
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import fetch_kline as tushare_source
from fetch_kline import (
    COOLDOWN_SECS,
    _get_kline_tushare,
    _looks_like_ip_ban,
    _resolve_date_arg,
    _setup_network_env,
    _setup_tushare_client,
    validate,
)
from web.core.config import ROOT
from web.schemas.market import FetchMarketRequest
from web.services import trading_calendar
import polars as pl


DATA_DIR = ROOT / "db"
STOCKLIST = ROOT / "stocklist.csv"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
MARKET_DATA_READY_TIME = dt.time(16, 0)
TRADE_CAL_LOOKBACK_DAYS = 45
DEFAULT_CALENDAR_START_YEAR = 2019
ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[..., None]
CancelCallback = Callable[[], bool]
_STOCKLIST_LOCK = threading.Lock()


class MarketDataCancelled(RuntimeError):
    pass


def fetch_market_data(
    payload: FetchMarketRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> dict[str, Any]:
    start = _resolve_date_arg(payload.start)
    end = _resolve_date_arg(payload.end)
    exclude_boards = set(payload.exclude_boards or [])

    _raise_if_cancelled(should_cancel)
    if progress:
        progress(0, 0, "正在初始化行情接口")
    if log:
        log("初始化行情接口")
    _setup_network_env()
    _setup_tushare_client()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stocklist_refresh = _refresh_stocklist_from_tushare(log=log)
    stocks = _load_stocks(STOCKLIST, exclude_boards)
    if not stocks:
        raise ValueError("stocklist 为空或过滤后无股票代码")

    started = time.time()
    failed = 0
    skipped_latest = 0
    empty = 0
    requested_end_date = _parse_date(end)
    expected_latest_date = _expected_latest_trade_date(log=log)
    target_end_date = min(requested_end_date, expected_latest_date)
    trading_calendar.ensure_coverage(
        pro=tushare_source.pro,
        start=_parse_date(start),
        end=target_end_date,
        log=log,
    )
    effective_end = _format_ts_date(target_end_date)
    if _parse_date(start) > target_end_date:
        raise ValueError(
            f"按北京时间 16:00 规则计算后，当前可用最新行情日为 {target_end_date.isoformat()}，"
            f"早于起始日期 {start}。请调小起始日期或稍后再试。"
        )
    total = len(stocks)
    if progress:
        progress(0, total, f"准备拉取 {total} 只股票")
    if log:
        log(
            f"行情拉取开始: {start} ~ {effective_end}，共 {total} 只股票；"
            f"请求结束日 {requested_end_date.isoformat()}，"
            f"按北京时间 16:00 规则判断的最新交易日 {expected_latest_date.isoformat()}"
        )

    for index, stock in enumerate(stocks, 1):
        _raise_if_cancelled(should_cancel)
        code = stock["code"]
        name = stock["name"]
        label = f"{code} {name}".strip()
        if progress:
            progress(index - 1, total, f"正在检查 {label}")

        local_latest = _local_latest_date(code)
        if local_latest is not None and local_latest >= target_end_date:
            skipped_latest += 1
            if progress:
                progress(index, total, f"{label} 已是最新")
            if log and (index == total or index % 100 == 0):
                log(
                    f"检查进度: {index}/{total}，已是最新 {skipped_latest}，"
                    f"需拉取 {index - skipped_latest - empty - failed}，空数据 {empty}，失败 {failed}"
                )
            continue

        if progress:
            progress(index - 1, total, f"正在拉取 {label}")
        status = _fetch_one_with_log(
            code,
            name,
            start,
            effective_end,
            DATA_DIR,
            log=log,
            should_cancel=should_cancel,
        )
        if status == "failed":
            failed += 1
        elif status == "empty":
            empty += 1

        if progress:
            progress(index, total, f"{label} 完成")
        if log and (index == total or index % 100 == 0):
            log(
                f"下载进度: {index}/{total}，已是最新 {skipped_latest}，"
                f"已拉取 {index - skipped_latest - empty - failed}，空数据 {empty}，失败 {failed}"
            )

    elapsed = time.time() - started
    if log:
        log(
            f"行情拉取完成: 总数 {total}，已是最新 {skipped_latest}，空数据 {empty}，失败 {failed}，耗时 {elapsed:.2f}s"
        )
    return {
        "start": start,
        "end": effective_end,
        "requested_end": end,
        "expected_latest_date": expected_latest_date.isoformat(),
        "stocklist_refreshed": stocklist_refresh["refreshed"],
        "stocklist_count": stocklist_refresh["count"],
        "exclude_boards": sorted(exclude_boards),
        "total": total,
        "skipped_latest": skipped_latest,
        "empty": empty,
        "failed_or_empty": failed + empty,
        "elapsed_seconds": elapsed,
        "data_dir": str(DATA_DIR),
    }


def get_market_data_status() -> dict[str, Any]:
    stocks = _load_stocks(STOCKLIST, set())
    parquet_files = list(DATA_DIR.glob("*.parquet")) if DATA_DIR.exists() else []
    latest_date = _market_latest_date()
    calendar_status = trading_calendar.coverage()
    return {
        "data_dir": str(DATA_DIR),
        "stocklist": str(STOCKLIST),
        "stock_count": len(stocks),
        "local_file_count": len(parquet_files),
        "latest_date": latest_date.isoformat() if latest_date else None,
        "calendar": calendar_status,
    }


def list_trading_dates(from_: str | None = None, to: str | None = None) -> dict[str, Any]:
    start = _parse_flexible_date(from_) if from_ else None
    requested_end = _parse_flexible_date(to) if to else None
    latest_data_date = _market_latest_date()
    if latest_data_date is None:
        return {
            "from": start.isoformat() if start else None,
            "to": requested_end.isoformat() if requested_end else None,
            "effective_to": None,
            "latest_data_date": None,
            "count": 0,
            "dates": [],
        }

    end = min(requested_end, latest_data_date) if requested_end else latest_data_date
    _ensure_trading_calendar_for_query(start=start, end=end)
    dates = trading_calendar.list_open_dates(start=start, end=end)
    if not dates:
        # Last-resort fallback for an unavailable Tushare API. This stays in
        # memory and is not persisted as the official trading calendar.
        trading_calendar.load_fallback_from_market_data(DATA_DIR)
        dates = trading_calendar.list_open_dates(start=start, end=end)
    return {
        "from": start.isoformat() if start else None,
        "to": requested_end.isoformat() if requested_end else None,
        "effective_to": end.isoformat(),
        "latest_data_date": latest_data_date.isoformat(),
        "count": len(dates),
        "dates": [day.isoformat() for day in dates],
    }


def load_trading_dates() -> list[dt.date]:
    latest_data_date = _market_latest_date()
    if latest_data_date is None:
        return []

    _ensure_trading_calendar_for_query(start=None, end=latest_data_date)
    dates = trading_calendar.list_open_dates(end=latest_data_date)
    if dates:
        return dates
    trading_calendar.load_fallback_from_market_data(DATA_DIR)
    return trading_calendar.list_open_dates(end=latest_data_date)


def _refresh_stocklist_from_tushare(*, log: LogCallback | None = None) -> dict[str, Any]:
    fields = ["ts_code", "symbol", "name", "area", "industry"]
    try:
        if tushare_source.pro is None:
            raise RuntimeError("Tushare client 未初始化")
        if log:
            log("正在更新股票列表 stocklist.csv")
        df = tushare_source.pro.stock_basic(
            exchange="",
            list_status="L",
            fields=",".join(fields),
        )
    except Exception as exc:  # noqa: BLE001 - kline sync can continue with the existing stocklist.
        if log:
            log(f"股票列表更新失败，继续使用现有 stocklist.csv: {exc}", "WARN")
        return {"refreshed": False, "count": _stocklist_count()}

    if df is None or df.empty:
        if log:
            log("股票列表更新返回空数据，继续使用现有 stocklist.csv", "WARN")
        return {"refreshed": False, "count": _stocklist_count()}

    for field in fields:
        if field not in df.columns:
            df[field] = ""

    df = df[fields].copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset=["symbol"]).sort_values("symbol")

    try:
        _write_stocklist(df, log=log)
    except Exception as exc:  # noqa: BLE001 - kline sync can continue with the existing stocklist.
        if log:
            log(f"股票列表写入失败，继续使用现有 stocklist.csv: {exc}", "WARN")
        return {"refreshed": False, "count": _stocklist_count()}

    count = len(df)
    if log:
        log(f"股票列表已更新: {count} 只股票")
    return {"refreshed": True, "count": count}


def _write_stocklist(df: Any, *, log: LogCallback | None = None) -> None:
    """Write stocklist with an atomic path, falling back for Docker file mounts."""
    STOCKLIST.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STOCKLIST.with_name(
        f".{STOCKLIST.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with _STOCKLIST_LOCK:
        try:
            df.to_csv(tmp_path, index=False, encoding="utf-8")
            tmp_path.replace(STOCKLIST)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            if exc.errno not in {errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES}:
                raise
            if log:
                log(
                    "stocklist.csv 无法原子替换，可能是 Docker 单文件挂载；"
                    "已改用直接覆盖写入。",
                    "WARN",
                )
            with STOCKLIST.open("w", encoding="utf-8", newline="") as fh:
                df.to_csv(fh, index=False)
        finally:
            tmp_path.unlink(missing_ok=True)


def _stocklist_count() -> int:
    try:
        with open(STOCKLIST, newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    except FileNotFoundError:
        return 0


def _fetch_one_with_log(
    code: str,
    name: str,
    start: str,
    end: str,
    out_dir,
    *,
    log: LogCallback | None,
    should_cancel: CancelCallback | None = None,
) -> str:
    label = f"{code} {name}".strip()
    parquet_path = out_dir / f"{code}.parquet"

    for attempt in range(1, 4):
        _raise_if_cancelled(should_cancel)
        try:
            pdf = _get_kline_tushare(code, start, end)
            if pdf.empty:
                if log:
                    log(f"{label} 返回空数据", "WARN")
                return "empty"
            pdf = validate(pdf)
            pl_df = _kline_pdf_to_polars(pdf)
            pl_df.write_parquet(parquet_path, compression="zstd")
            return "ok"
        except Exception as exc:  # noqa: BLE001 - retry details should be visible in console.
            if _looks_like_ip_ban(exc):
                if log:
                    log(
                        f"{label} 第 {attempt} 次抓取疑似限流，冷却 {COOLDOWN_SECS} 秒: {exc}",
                        "WARN",
                    )
                _sleep_with_cancel(COOLDOWN_SECS, should_cancel)
            else:
                silent_seconds = 15 * attempt
                if log:
                    log(
                        f"{label} 第 {attempt} 次抓取失败，{silent_seconds} 秒后重试: {exc}",
                        "WARN",
                    )
                _sleep_with_cancel(silent_seconds, should_cancel)

    if log:
        log(f"{label} 三次抓取均失败，已跳过", "ERROR")
    return "failed"


def _raise_if_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel and should_cancel():
        raise MarketDataCancelled("任务已停止")


def _sleep_with_cancel(seconds: int | float, should_cancel: CancelCallback | None) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        _raise_if_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _kline_pdf_to_polars(pdf: Any) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [_coerce_kline_date(value) for value in pdf["date"].tolist()],
            "open": _float_column(pdf, "open"),
            "close": _float_column(pdf, "close"),
            "high": _float_column(pdf, "high"),
            "low": _float_column(pdf, "low"),
            "volume": _float_column(pdf, "volume"),
        },
        schema={
            "date": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "volume": pl.Float64,
        },
    ).sort("date")


def _coerce_kline_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    if hasattr(value, "date") and callable(value.date):
        return value.date()

    text = str(value).strip()
    if "-" in text:
        return dt.datetime.strptime(text[:10], "%Y-%m-%d").date()
    return dt.datetime.strptime(text[:8], "%Y%m%d").date()


def _float_column(pdf: Any, column: str) -> list[float | None]:
    values = []
    for value in pdf[column].tolist():
        if value is None:
            values.append(None)
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            values.append(None)
    return values


def _load_stocks(stocklist_csv, exclude_boards: set[str]) -> list[dict[str, str]]:
    stocks: list[dict[str, str]] = []
    with open(stocklist_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw_code = str(row.get("symbol") or "").strip()
            if not raw_code:
                continue
            code = raw_code.zfill(6)
            ts_code = str(row.get("ts_code") or "").upper()
            if _is_excluded_board(code, ts_code, exclude_boards):
                continue
            stocks.append(
                {
                    "code": code,
                    "name": str(row.get("name") or "").strip(),
                }
            )

    deduped: dict[str, dict[str, str]] = {}
    for stock in stocks:
        deduped.setdefault(stock["code"], stock)
    return list(deduped.values())


def _is_excluded_board(code: str, ts_code: str, exclude_boards: set[str]) -> bool:
    if "gem" in exclude_boards and code.startswith(("300", "301")):
        return True
    if "star" in exclude_boards and code.startswith("688"):
        return True
    if "bj" in exclude_boards and (ts_code.endswith(".BJ") or code.startswith(("4", "8"))):
        return True
    return False


def _expected_latest_trade_date(
    *,
    now: dt.datetime | None = None,
    log: LogCallback | None = None,
) -> dt.date:
    china_now = now.astimezone(CHINA_TZ) if now else dt.datetime.now(CHINA_TZ)
    cutoff_date = china_now.date()
    if china_now.time() < MARKET_DATA_READY_TIME:
        cutoff_date -= dt.timedelta(days=1)

    trading_calendar.ensure_coverage(
        pro=tushare_source.pro,
        start=cutoff_date - dt.timedelta(days=TRADE_CAL_LOOKBACK_DAYS),
        end=cutoff_date,
        log=log,
    )
    latest_open_date = trading_calendar.latest_open_date_on_or_before(cutoff_date)
    if latest_open_date:
        return latest_open_date
    return _previous_weekday(cutoff_date)


def _previous_weekday(value: dt.date) -> dt.date:
    current = value
    while current.weekday() >= 5:
        current -= dt.timedelta(days=1)
    return current


def _ensure_trading_calendar_for_query(
    *,
    start: dt.date | None,
    end: dt.date | None,
) -> None:
    range_start, range_end = _calendar_query_range(start=start, end=end)
    if trading_calendar.has_official_coverage(start=range_start, end=range_end):
        return
    _setup_network_env()
    _setup_tushare_client()
    result = trading_calendar.ensure_coverage(
        pro=tushare_source.pro,
        start=range_start,
        end=range_end,
    )
    if result.get("error"):
        # Keep the endpoint usable when Tushare is temporarily unavailable.
        # The caller will fall back to in-memory market-data-derived dates.
        return


def _calendar_query_range(
    *,
    start: dt.date | None,
    end: dt.date | None,
) -> tuple[dt.date, dt.date]:
    china_today = dt.datetime.now(CHINA_TZ).date()
    range_start = start or dt.date(DEFAULT_CALENDAR_START_YEAR, 1, 1)
    range_end = end or dt.date(china_today.year, 12, 31)
    if range_start > range_end:
        return range_end, range_start
    return range_start, range_end


def _local_latest_date(code: str) -> dt.date | None:
    path = DATA_DIR / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        value = pl.scan_parquet(path).select(pl.col("date").max().alias("max_date")).collect()["max_date"][0]
    except Exception:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def _market_latest_date() -> dt.date | None:
    latest: dt.date | None = None
    for stock in _load_stocks(STOCKLIST, set())[:24]:
        local_latest = _local_latest_date(stock["code"])
        if local_latest is not None and (latest is None or local_latest > latest):
            latest = local_latest
    return latest


def _market_trade_dates() -> list[dt.date]:
    dates = trading_calendar.list_open_dates()
    if dates:
        return dates
    trading_calendar.seed_from_market_data(DATA_DIR)
    return trading_calendar.list_open_dates()


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(str(value), "%Y%m%d").date()


def _format_ts_date(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def _parse_flexible_date(value: str) -> dt.date:
    text = str(value).strip()
    if "-" in text:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    return dt.datetime.strptime(text, "%Y%m%d").date()
