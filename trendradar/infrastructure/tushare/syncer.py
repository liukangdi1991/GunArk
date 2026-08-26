"""Tushare daily kline syncer with rate limiting, retry, and atomic write."""

import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import polars as pl

from trendradar.infrastructure.tushare.client import get_pro

logger = logging.getLogger(__name__)

IP_BAN_ERROR_MSG = "每分钟最多访问该接口"
RATE_LIMIT_MSG = "频率超限"  # matches Tushare "访问接口(daily)频率超限(300次/分钟)"



def decide_mode(missing_days: int, force: bool, retry_codes: list) -> str:
    """full (by-stock backfill/retry) vs incremental (per-day) mode.

    retry_codes non-empty always forces full so leftover failures get
    re-synced; force or a calendar gap > 20 days also selects full.
    """
    if retry_codes:
        return "full"
    if force or missing_days > 20:
        return "full"
    return "incremental"


@dataclass(frozen=True)
class SyncPlan:
    """Sync decision + execution context, computed once by plan_sync."""

    mode: str
    missing_days: int
    missing_dates: list  # list[date]
    start: date
    end: date
    latest: date
    all_trade: set  # set[date]
    done: set  # set[date]
    uptodate: bool
    force: bool
    retry_codes: list  # list[str]


def plan_sync(pro, bars_dir: Path, cache_dir: Path, request: dict,
              now_utc=None) -> SyncPlan:
    """Compute the sync plan (mode, gap, context) before executing.

    Single decision source: sync_market consumes the returned SyncPlan and
    does not recompute mode/missing. Loading mirrors the previous sync_market
    head: calendar (fetch → cache → fail), done ledger (+ legacy first-run
    cross-check), req_start derivation, up-to-date short-circuit.
    """
    from trendradar.infrastructure.tushare.calendar import (
        fetch_trade_calendar, load_trade_calendar, save_trade_calendar,
    )
    from trendradar.infrastructure.tushare.markers import (
        load_sync_done, save_sync_done, load_retry_codes,
    )

    now = now_utc or datetime.now(timezone.utc)
    today = now.astimezone(_SHANGHAI).date()
    force = bool(request.get("force"))

    cal_path = cache_dir / "trade_calendar.parquet"
    done_path = cache_dir / "sync_done.json"
    retry_path = cache_dir / "sync_retry_codes.json"
    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Authoritative calendar: fetch now, fall back to cache, else fail.
    req_start = date.fromisoformat(request["start_date"]) if request.get("start_date") else None
    req_end = date.fromisoformat(request["end_date"]) if request.get("end_date") else None
    if req_end is None:
        req_end = today
    try:
        all_trade = set(fetch_trade_calendar(pro, req_start or date(1990, 1, 1), req_end))
        save_trade_calendar(cal_path, sorted(all_trade))
    except Exception:
        all_trade = set(load_trade_calendar(cal_path) or [])
        if not all_trade:
            raise RuntimeError("trade calendar unavailable (fetch failed and no cache)")

    latest = latest_tradeable_day(all_trade, now)
    req_end = min(req_end, latest)  # clamp future end

    done = load_sync_done(done_path)
    if not done and bars_dir.exists() and any(bars_dir.glob("*.parquet")):
        # Legacy first run: strict cross-check — mark only dates present in
        # EVERY bar file (intersection).
        from trendradar.domain.market.data_store import LocalParquetMarketStore
        store = LocalParquetMarketStore(bars_dir)
        cal = set(store.get_calendar())
        files = sorted(bars_dir.glob("*.parquet"))
        common = cal
        for p in files:
            try:
                common &= set(pl.read_parquet(p, columns=["date"])["date"].to_list())
            except Exception:
                pass
        done = common & all_trade
        save_sync_done(done_path, done)

    if req_start is None:
        if bars_dir.exists() and any(bars_dir.glob("*.parquet")):
            local_min = min(set(load_sync_done(done_path)) or [req_end]) if done else None
            if local_min is None:
                store = LocalParquetMarketStore(bars_dir)
                dates = store.get_calendar()
                local_min = dates[0] if dates else req_end
            req_start = local_min
        else:
            req_start = date(1990, 1, 1)

    retry_codes = load_retry_codes(retry_path)
    uptodate = (not force) and is_up_to_date(
        local_min=req_start, local_max=latest,
        req_start=None if request.get("start_date") is None else req_start,
        latest=latest, done=done, trade_days=all_trade,
    )
    if uptodate:
        missing_dates = []
        missing_days = 0
    else:
        missing_dates = missing_trade_days(all_trade, done, req_start, req_end)
        missing_days = len(missing_dates)

    mode = decide_mode(missing_days, force, retry_codes)
    return SyncPlan(
        mode=mode, missing_days=missing_days, missing_dates=missing_dates,
        start=req_start, end=req_end, latest=latest, all_trade=all_trade,
        done=done, uptodate=uptodate, force=force, retry_codes=retry_codes,
    )


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


def _to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "900", "901")):
        return f"{code}.SH"          # 沪主板/科创板/沪B股
    elif code.startswith(("92", "4", "8")):
        return f"{code}.BJ"          # 北交所（92 新编号 + 4/8 旧编号）
    return f"{code}.SZ"              # 深主板/创业板


def _fetch_with_retry(
    pro, code: str, start: date, end: date, max_retries: int,
    bucket=None, cancel_check=None,
) -> Optional[pl.DataFrame]:
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    params = {
        "ts_code": _to_ts_code(code),
        "start_date": start_s,
        "end_date": end_s,
        "freq": "D",
    }

    for attempt in range(max_retries):
        if bucket is not None:
            if not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
                # Starved/timed out: fail this code; the retry loop (or next
                # sync run) re-attempts it later. Never block the job forever.
                return None
        try:
            resp = pro.daily(**params)
            return _response_to_df(resp, code)
        except Exception as e:
            msg = str(e)
            if RATE_LIMIT_MSG in msg:
                # Per-window limit (300/min): abandon this code to the retry
                # loop. Hot-retrying here only adds load into the same window
                # and multiplies the violation.
                logger.warning("Rate limit on %s: %s", code, msg)
                return None
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


def _response_to_df(resp, code: str) -> pl.DataFrame:
    if resp is None or not resp.to_dict(orient="list"):
        return pl.DataFrame()

    df = pl.DataFrame(resp.to_dict(orient="list"))

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

    for col_name in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Float64))

    df = df.with_columns(
        pl.lit(code).cast(pl.Utf8).alias("code"),
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
    )

    cols = ["code", "date", "open", "high", "low", "close", "volume", "amount", "pre_close", "adj_factor", "is_suspended"]
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


# ---------------------------------------------------------------------------
# Incremental sync planning (pure helpers)
# ---------------------------------------------------------------------------

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SHARD_MAX_ROWS = 5500


def latest_tradeable_day(trade_days: set, now_utc: datetime) -> date:
    """Latest trading day whose data is available (Beijing 16:00 cutoff)."""
    beijing = now_utc.astimezone(_SHANGHAI)
    today = beijing.date()
    candidate = today if beijing.hour >= 16 and today in trade_days else None
    if candidate is not None:
        return candidate
    past = sorted(d for d in trade_days if d < today)
    return past[-1] if past else today


def is_up_to_date(
    local_min: date,
    local_max: date,
    req_start: date | None,
    latest: date,
    done: set,
    trade_days: set,
) -> bool:
    """Cheap max/min precheck + authoritative sync_done coverage check."""
    if local_max < latest:
        return False
    if req_start is not None and req_start < local_min:
        return False
    end = latest
    start = req_start if req_start is not None else local_min
    missing = missing_trade_days(trade_days, done, start, end)
    return not missing


def missing_trade_days(
    trade_days: set, done: set, start: date, end: date
) -> list[date]:
    """Trade days in [start, end] not marked done, ascending."""
    return sorted(
        d for d in trade_days if start <= d <= end and d not in done
    )


def shard_ranges(start: date, end: date, max_rows: int = _SHARD_MAX_ROWS) -> list:
    """Split [start, end] into date ranges each estimated under max_rows."""
    total_days = (end - start).days + 1
    est_trade_days = int(total_days / 7 * 5)
    if est_trade_days <= max_rows:
        return [(start, end)]
    shards = -(-est_trade_days // max_rows)  # ceil
    span = -(-total_days // shards)  # ceil
    ranges = []
    cur = start
    while cur <= end:
        seg_end = min(end, cur + timedelta(days=span - 1))
        ranges.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return ranges


_BOARD_PREFIXES = {
    "gem": ("300", "301"),
    "star": ("688", "689"),
    "bj": ("4", "8"),
}


def _exclude_prefixes(exclude_boards) -> tuple | None:
    if not exclude_boards:
        return None
    prefixes = []
    for b in exclude_boards:
        prefixes.extend(_BOARD_PREFIXES.get(b, ()))
    return tuple(prefixes) or None


def _filter_excluded_boards(df, exclude_boards):
    """Drop rows whose code belongs to an excluded board (incremental path)."""
    prefixes = _exclude_prefixes(exclude_boards)
    if prefixes is None or df.is_empty():
        return df
    expr = ~pl.col("code").str.starts_with(prefixes[0])
    for p in prefixes[1:]:
        expr = expr & ~pl.col("code").str.starts_with(p)
    return df.filter(expr)


def _filter_excluded_codes(codes: list[str], exclude_boards) -> list[str]:
    """Drop codes belonging to an excluded board (by-stock path)."""
    prefixes = _exclude_prefixes(exclude_boards)
    if prefixes is None:
        return codes
    return [c for c in codes if not c.startswith(prefixes)]


def merge_day_bars(day_df, bars_dir: Path) -> list[str]:
    """Merge one full-market day frame into per-code parquet files.

    New rows overwrite local same-date rows. Returns codes written (new + updated).
    """
    bars_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for code in day_df["code"].unique().to_list():
        group = day_df.filter(pl.col("code") == code)
        target = bars_dir / f"{code}.parquet"
        if target.exists():
            local = pl.read_parquet(target)
            merged = pl.concat(
                [local.filter(~pl.col("date").is_in(group["date"].implode())), group]
            ).sort("date")
        else:
            merged = group.sort("date")
        _atomic_write_parquet(merged, target)
        written.append(str(code))
    return written


def sync_by_stock(
    pro,
    codes: list[str],
    start: date,
    end: date,
    bars_dir: Path,
    done_path: Path,
    retry_path: Path,
    progress=None,
    cancel_check=None,
    bucket=None,
    max_workers: int = 6,
    retry_failed: bool = True,
) -> dict:
    """Fetch full history per stock (sharded), merge into bars.

    All-or-nothing day marking: only when every stock in this batch succeeds
    are [start, end] trade days written to sync_done; failures persist to
    retry_path so the next run retries just the failed subset.
    """
    from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar
    from trendradar.infrastructure.tushare.markers import (
        load_retry_codes, load_sync_done, save_retry_codes, save_sync_done,
    )
    from trendradar.infrastructure.tushare.rate_limit import TokenBucket
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if bucket is None:
        bucket = TokenBucket()
    trade_days = set(fetch_trade_calendar(pro, start, end))
    if retry_failed:
        codes = load_retry_codes(retry_path) or codes

    def fetch_one(code: str) -> bool:
        for seg_start, seg_end in shard_ranges(start, end):
            if cancel_check and cancel_check():
                return False
            data = _fetch_with_retry(pro, code, seg_start, seg_end, 3,
                                     bucket=bucket, cancel_check=cancel_check)
            if data is None:
                return False
            if data.is_empty():
                continue
            target = bars_dir / f"{code}.parquet"
            if target.exists():
                local = pl.read_parquet(target)
                merged = pl.concat(
                    [local.filter(~pl.col("date").is_in(data["date"])), data]
                ).sort("date")
            else:
                merged = data.sort("date")
            _atomic_write_parquet(merged, target)
        return True

    failed = []
    total = len(codes)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), start=1):
            code = futures[fut]
            if progress:
                progress(i, total, code)
            if cancel_check and cancel_check():
                break
            try:
                if not fut.result():
                    failed.append(code)
            except Exception:
                failed.append(code)

    if failed:
        save_retry_codes(retry_path, failed)
        done = set()
    else:
        save_retry_codes(retry_path, [])
        done = load_sync_done(done_path) | trade_days
        save_sync_done(done_path, done)

    return {"failed_codes": failed}


def _fetch_daily_by_date(pro, day: date):
    """Fetch one full-market trading day and normalize to bar schema."""
    resp = pro.daily(trade_date=day.strftime("%Y%m%d"))
    if resp is None:
        return None  # 接口无响应：调用方不标 done，留待下轮重试
    if isinstance(resp, pl.DataFrame):
        if resp.is_empty():
            return pl.DataFrame()
        df = resp
    else:
        if not resp.to_dict(orient="list"):
            return pl.DataFrame()
        df = pl.DataFrame(resp.to_dict(orient="list"))
    df = df.rename(
        {c: {"trade_date": "date", "vol": "volume"}.get(c, c)
         for c in df.columns if c in ("trade_date", "vol")}
    )
    df = df.with_columns(
        pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
    )
    for col in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64))
    df = df.with_columns(
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )
    cols = ["code", "date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "adj_factor", "is_suspended"]
    return df.select([c for c in cols if c in df.columns]).sort("date")


def sync_market(
    pro,
    bars_dir: Path,
    cache_dir: Path,
    request: dict,
    now_utc=None,
    progress=None,
    cancel_check=None,
    plan=None,                        # SyncPlan | None — computed here when omitted
    retry_interval: float = 30.0,     # seconds between retry rounds (0 in tests)
    max_retry_rounds: int = 9,        # retry rounds after the initial pass (10 total batches)
) -> dict:
    """Top-level incremental sync orchestrator.

    request keys: start_date, end_date, codes, force.
    Mode/gap decisions come from plan_sync (single source); this function
    only executes. Returns result_json stats (mode, missing_days,
    synced_days, synced_codes, new_codes, failed_days, failed_codes,
    retry_rounds, skipped_uptodate).
    """
    from trendradar.infrastructure.tushare.markers import (
        load_retry_codes, save_sync_done,
    )

    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    done_path = cache_dir / "sync_done.json"
    retry_path = cache_dir / "sync_retry_codes.json"

    if plan is None:
        plan = plan_sync(pro, bars_dir, cache_dir, request, now_utc=now_utc)

    if plan.uptodate and not request.get("codes"):
        # 指定 codes 时绕过 up-to-date 短路：新上市/缺失代码即使日期范围已覆盖也要拉
        return {"mode": "incremental", "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": True}

    if plan.mode == "incremental" and not request.get("codes"):
        # Daily path（指定 codes 时强制 by_stock：按代码同步不受 mode 约束）
        from trendradar.infrastructure.tushare.rate_limit import TokenBucket
        bucket = TokenBucket()
        done = set(plan.done)
        failed_days = 0
        synced_days = 0
        synced_codes = 0
        new_codes = 0
        missing = plan.missing_dates
        total = len(missing)
        # 新上市补齐（尽力而为）：股票列表有但无 bars 的代码先拉；
        # 优先读 worker 刚刷新的 stock_meta.parquet（避免重复调 stock_basic），
        # 本地无列表时回退拉取；全程失败不影响日路径主流程
        try:
            meta_path = bars_dir.parent / "stock_meta.parquet"
            if meta_path.exists():
                meta = pl.read_parquet(meta_path)
            else:
                from trendradar.infrastructure.tushare.stocklist import sync_stock_list
                meta = sync_stock_list(bars_dir)
            all_codes = meta["code"].to_list() if not meta.is_empty() else []
            existing = {p.stem for p in bars_dir.glob("*.parquet")}
            new_listed = [c for c in all_codes if c not in existing]
            if new_listed:
                if progress:
                    progress(0, len(new_listed), f"补齐 {len(new_listed)} 只新上市股票")
                before = {p.name for p in bars_dir.glob("*.parquet")}
                sync_by_stock(pro, new_listed, plan.start, plan.end, bars_dir,
                              done_path, retry_path, progress, cancel_check,
                              retry_failed=False)
                after = {p.name for p in bars_dir.glob("*.parquet")}
                new_codes += len(after - before)
        except Exception:
            pass  # 尽力而为：失败则本次不补，下次同步再试
        for idx, day in enumerate(missing, start=1):
            if cancel_check and cancel_check():
                break
            if progress:
                progress(idx, total, str(day))
            if not bucket.acquire(cancel_check=cancel_check):
                break
            try:
                df = _fetch_daily_by_date(pro, day)
            except Exception as e:
                # 限流/IP 封禁等接口异常：不标 done，本轮跳过，下轮重试
                logger.warning("daily fetch failed for %s: %s", day, e)
                continue
            if df is None:
                continue  # 无响应：不标 done
            df = _filter_excluded_boards(df, request.get("exclude_boards"))
            if df.is_empty():
                if day == plan.latest:
                    continue  # not done, warning-level, retried next run
                done.add(day)
                save_sync_done(done_path, done)
                continue
            before = {p.name for p in bars_dir.glob("*.parquet")}
            written = merge_day_bars(df, bars_dir)
            after = {p.name for p in bars_dir.glob("*.parquet")}
            new_codes += len(set(after) - before)
            synced_codes += len(written)
            done.add(day)
            save_sync_done(done_path, done)
            synced_days += 1

        return {"mode": "incremental", "missing_days": len(missing),
                "synced_days": synced_days, "synced_codes": synced_codes,
                "new_codes": new_codes, "failed_days": failed_days,
                "failed_codes": len(load_retry_codes(retry_path)),
                "retry_rounds": 0, "skipped_uptodate": False}

    # By-stock path (full / large gap / retry subset)
    from trendradar.infrastructure.tushare.stocklist import sync_stock_list
    meta = sync_stock_list(bars_dir)
    req_codes = request.get("codes")
    if req_codes:
        codes = list(req_codes)   # 指定 codes：只同步这些
    else:
        codes = meta["code"].to_list() if not meta.is_empty() else []
    codes = _filter_excluded_codes(codes, request.get("exclude_boards"))
    result = sync_by_stock(
        pro, codes, plan.start, plan.end, bars_dir,
        done_path, retry_path, progress, cancel_check,
    )
    failed = result["failed_codes"]
    retry_rounds = 0
    while failed and retry_rounds < max_retry_rounds:
        retry_rounds += 1
        if progress:
            progress(0, len(failed),
                     f"[重试 {retry_rounds}/{max_retry_rounds}] 开始：{len(failed)} 个失败代码")
        # Cancellable 30s gap (1s granularity)
        for _ in range(int(retry_interval)):
            if cancel_check and cancel_check():
                break
            time.sleep(1)
        if cancel_check and cancel_check():
            break

        def _sub_progress(cur, total, msg):
            if progress:
                progress(cur, total, f"[重试 {retry_rounds}/{max_retry_rounds}] {msg}")

        sub = sync_by_stock(pro, failed, plan.start, plan.end, bars_dir,
                            done_path, retry_path, _sub_progress, cancel_check,
                            retry_failed=False)
        failed = sub["failed_codes"]
        if not failed and progress:
            progress(0, 0, "全部失败代码已补完")

    return {"mode": "full", "missing_days": plan.missing_days,
            "synced_days": 0, "synced_codes": len(codes) - len(failed),
            "new_codes": 0, "failed_days": 0, "failed_codes": len(failed),
            "retry_rounds": retry_rounds, "skipped_uptodate": False}
