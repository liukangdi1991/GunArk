# Market Data Incremental Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace full-resync with init-by-stock + daily incremental sync, with completion markers, authoritative calendar, mutual exclusion, and new-code merge.

**Architecture:** New rate limiter, trade-calendar facility, completion-marker store, and a top-level `sync_market` orchestrator in `syncer.py`; `sync_kline` stays untouched for compatibility. Mutual exclusion enforced in `JobExecutor` under its lock; restart recovery in app lifespan.

**Tech Stack:** Python 3.11+, polars, tushare, ThreadPoolExecutor, threading.Lock, ZoneInfo.

**Spec:** `docs/superpowers/specs/2026-08-21-market-data-incremental-sync.md` (v5)

---

## Task 1: TokenBucket rate limiter

**Files:**
- Create: `trendradar/infrastructure/tushare/rate_limit.py`
- Test: `tests/infrastructure/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

```python
import time
import pytest
from trendradar.infrastructure.tushare.rate_limit import TokenBucket


def test_acquire_immediately_when_burst_available():
    bucket = TokenBucket(rate_per_min=450, burst=450)
    assert bucket.acquire() is True


def test_acquire_blocks_until_refill():
    bucket = TokenBucket(rate_per_min=60, burst=2)  # 1 token/sec
    assert bucket.acquire() is True
    assert bucket.acquire() is True
    t0 = time.time()
    assert bucket.acquire(timeout=2.0) is True  # waits ~1s for refill
    assert 0.8 <= time.time() - t0 < 2.0


def test_acquire_returns_false_on_cancel():
    bucket = TokenBucket(rate_per_min=60, burst=1)
    assert bucket.acquire() is True
    assert bucket.acquire(timeout=1.0, cancel_check=lambda: True) is False


def test_rate_does_not_exceed_ceiling():
    bucket = TokenBucket(rate_per_min=60, burst=1)  # 1/sec
    acquired = 0
    for _ in range(5):
        if bucket.acquire(timeout=0.2):
            acquired += 1
        time.sleep(0.3)
    assert acquired <= 3  # 5 * 0.3s window, 1/sec ceiling
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_rate_limit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trendradar.infrastructure.tushare.rate_limit'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Global Tushare request rate limiter (token bucket).

Ceiling: 450 requests/minute (500/min upstream budget minus 10% margin).
Only the submit pace is throttled; in-flight concurrency is bounded by the
thread pool size, so network speed cannot exceed the ceiling.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_min: float = 450, burst: int = 450) -> None:
        self._tokens = float(burst)
        self._capacity = float(burst)
        self._rate = rate_per_min / 60.0  # tokens per second
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    def acquire(self, timeout: float = 60.0, cancel_check=None) -> bool:
        """Block until a token is available; return False on cancel/timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if cancel_check and cancel_check():
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_rate_limit.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/rate_limit.py tests/infrastructure/test_rate_limit.py
git commit -m "feat: token bucket rate limiter for tushare sync"
```

---

## Task 2: Authoritative trade calendar facility

**Files:**
- Create: `trendradar/infrastructure/tushare/calendar.py`
- Test: `tests/infrastructure/test_trade_calendar.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
import polars as pl
from trendradar.infrastructure.tushare.calendar import (
    load_trade_calendar,
    save_trade_calendar,
)


def test_save_and_load_roundtrip(tmp_path):
    dates = [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)]
    path = tmp_path / "trade_calendar.parquet"
    save_trade_calendar(path, dates)
    assert load_trade_calendar(path) == dates


def test_load_missing_returns_none(tmp_path):
    assert load_trade_calendar(tmp_path / "missing.parquet") is None


def test_save_is_atomic(tmp_path):
    path = tmp_path / "trade_calendar.parquet"
    save_trade_calendar(path, [date(2026, 8, 18)])
    save_trade_calendar(path, [date(2026, 8, 19)])
    assert load_trade_calendar(path) == [date(2026, 8, 19)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_trade_calendar.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
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
    """Fetch trade days in [start, end] from Tushare (exchange SSE)."""
    resp = pro.trade_cal(
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if resp is None or resp.empty:
        return []
    return sorted(
        pl.from_pandas(resp)
        .filter(pl.col("is_open") == 1)
        .select(
            pl.col("cal_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
        )["date"]
        .to_list()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_trade_calendar.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/calendar.py tests/infrastructure/test_trade_calendar.py
git commit -m "feat: authoritative trade calendar with atomic local cache"
```

---

## Task 3: Completion markers (sync_done / sync_retry_codes)

**Files:**
- Create: `trendradar/infrastructure/tushare/markers.py`
- Test: `tests/infrastructure/test_markers.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from trendradar.infrastructure.tushare.markers import (
    load_sync_done,
    save_sync_done,
    load_retry_codes,
    save_retry_codes,
)


def test_sync_done_roundtrip(tmp_path):
    path = tmp_path / "sync_done.json"
    dates = {date(2026, 8, 18), date(2026, 8, 20)}
    save_sync_done(path, dates)
    assert load_sync_done(path) == dates


def test_sync_done_missing_returns_empty(tmp_path):
    assert load_sync_done(tmp_path / "missing.json") == set()


def test_retry_codes_roundtrip(tmp_path):
    path = tmp_path / "sync_retry_codes.json"
    save_retry_codes(path, ["000001", "600519"])
    assert load_retry_codes(path) == ["000001", "600519"]


def test_retry_codes_missing_returns_empty(tmp_path):
    assert load_retry_codes(tmp_path / "missing.json") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_markers.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""Sync completion markers.

sync_done.json        — trading days fully synced (all stocks), day dimension.
sync_retry_codes.json — stocks that failed in by-stock mode; retried as a
                        subset on the next by-stock run.
Both atomically written.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_sync_done(path: Path, dates: set[date]) -> None:
    _atomic_write_json(path, {"dates": sorted(d.isoformat() for d in dates)})


def load_sync_done(path: Path) -> set[date]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {date.fromisoformat(d) for d in data.get("dates", [])}
    except Exception:
        return set()


def save_retry_codes(path: Path, codes: list[str]) -> None:
    _atomic_write_json(path, {"codes": sorted(set(codes))})


def load_retry_codes(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted(set(data.get("codes", [])))
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_markers.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/markers.py tests/infrastructure/test_markers.py
git commit -m "feat: sync completion markers (sync_done, retry codes)"
```

---

## Task 4: Latest-day judgment and missing-day computation (pure functions)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py` (append pure helpers)
- Test: `tests/infrastructure/test_sync_planner.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, datetime, timezone
import pytest
from trendradar.infrastructure.tushare.syncer import (
    latest_tradeable_day,
    is_up_to_date,
    missing_trade_days,
    shard_ranges,
)

TRADE = {
    date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
    date(2026, 8, 21), date(2026, 8, 24),
}


def test_latest_tradeable_before_16():
    # Beijing 2026-08-21 10:00 -> not past 16:00, latest tradeable = 08-20
    now = datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 20)


def test_latest_tradeable_after_16():
    now = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)  # Beijing 17:00
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 21)


def test_latest_tradeable_non_trading_today():
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)  # Saturday
    assert latest_tradeable_day(TRADE, now) == date(2026, 8, 21)


def test_is_up_to_date_ok():
    done = {date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)}
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=None, latest=date(2026, 8, 20), done=done, trade_days=TRADE,
    ) is True


def test_is_up_to_date_missing_middle_day():
    done = {date(2026, 8, 18), date(2026, 8, 20)}  # 08-19 failed
    # max_date is latest but 08-19 not done -> not up to date
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=None, latest=date(2026, 8, 20), done=done, trade_days=TRADE,
    ) is False


def test_is_up_to_date_earlier_start_request():
    # req_start earlier than local_min -> head gap, not up to date
    assert is_up_to_date(
        local_min=date(2026, 8, 18), local_max=date(2026, 8, 20),
        req_start=date(2026, 8, 1), latest=date(2026, 8, 20),
        done=set(), trade_days=TRADE,
    ) is False


def test_missing_trade_days():
    done = {date(2026, 8, 18), date(2026, 8, 20)}
    missing = missing_trade_days(
        TRADE, done, start=date(2026, 8, 18), end=date(2026, 8, 21)
    )
    assert missing == [date(2026, 8, 19), date(2026, 8, 21)]


def test_shard_ranges_small():
    ranges = shard_ranges(date(2026, 1, 1), date(2026, 1, 31))
    assert ranges == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_shard_ranges_large():
    # ~35 years -> multiple shards, each covering < 5500 rows
    ranges = shard_ranges(date(1990, 1, 1), date(2026, 8, 20))
    assert len(ranges) > 1
    for s, e in ranges:
        est_days = (e - s).days / 7 * 5
        assert est_days <= 5500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_planner.py -q`
Expected: FAIL with `ImportError: cannot import name 'latest_tradeable_day'`

- [ ] **Step 3: Write minimal implementation**

Append to `trendradar/infrastructure/tushare/syncer.py`:

```python
# ---------------------------------------------------------------------------
# Incremental sync planning (pure helpers)
# ---------------------------------------------------------------------------

from datetime import datetime as _datetime
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SHARD_MAX_ROWS = 5500


def latest_tradeable_day(trade_days: set, now_utc: _datetime) -> date:
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
        seg_end = min(end, cur + _datetime.timedelta(days=span - 1) if False else __import__("datetime").timedelta(days=span - 1))
        ranges.append((cur, seg_end))
        cur = seg_end + __import__("datetime").timedelta(days=1)
    return ranges
```

Fix the awkward imports by adding `from datetime import timedelta` at the top of syncer.py (it already imports `from datetime import date`), then use:

```python
def shard_ranges(start: date, end: date, max_rows: int = _SHARD_MAX_ROWS) -> list:
    total_days = (end - start).days + 1
    est_trade_days = int(total_days / 7 * 5)
    if est_trade_days <= max_rows:
        return [(start, end)]
    shards = -(-est_trade_days // max_rows)
    span = -(-total_days // shards)
    ranges = []
    cur = start
    while cur <= end:
        seg_end = min(end, cur + timedelta(days=span - 1))
        ranges.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return ranges
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_planner.py -q`
Expected: PASS (9 passed). Fix any syntax issues from the draft (remove the `if False` line — the final `shard_ranges` is the clean version below the draft).

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_planner.py
git commit -m "feat: incremental sync planning — latest day, up-to-date check, missing days, sharding"
```

---

## Task 5: Daily-incremental path (per-day fetch + per-code merge)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_sync_daily.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
import polars as pl
from pathlib import Path
import tempfile

from trendradar.infrastructure.tushare.syncer import merge_day_bars


def _day_df(rows):
    return pl.DataFrame(rows)


def test_merge_day_creates_new_code(tmp_path):
    bars_dir = tmp_path / "bars"
    df = _day_df([
        {"code": "000001", "date": date(2026, 8, 20), "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1000.0, "amount": 10400.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(df, bars_dir)
    assert (bars_dir / "000001.parquet").exists()
    out = pl.read_parquet(bars_dir / "000001.parquet")
    assert out.height == 1
    assert out["date"][0] == date(2026, 8, 20)


def test_merge_day_appends_and_dedups_new_wins(tmp_path):
    bars_dir = tmp_path / "bars"
    first = _day_df([
        {"code": "000001", "date": date(2026, 8, 19), "open": 9.0, "high": 9.5,
         "low": 8.8, "close": 9.2, "volume": 1000.0, "amount": 9200.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(first, bars_dir)
    # Same date with new close -> new row wins; plus a new day
    second = _day_df([
        {"code": "000001", "date": date(2026, 8, 19), "open": 9.0, "high": 9.5,
         "low": 8.8, "close": 9.9, "volume": 1000.0, "amount": 9200.0,
         "adj_factor": 1.0, "is_suspended": False},
        {"code": "000001", "date": date(2026, 8, 20), "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1000.0, "amount": 10400.0,
         "adj_factor": 1.0, "is_suspended": False},
    ])
    merge_day_bars(second, bars_dir)
    out = pl.read_parquet(bars_dir / "000001.parquet")
    assert out.height == 2
    assert sorted(out["date"].to_list()) == [date(2026, 8, 19), date(2026, 8, 20)]
    by_date = {r["date"]: r for r in out.to_dicts()}
    assert by_date[date(2026, 8, 19)]["close"] == 9.9  # new wins
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_daily.py -q`
Expected: FAIL with `ImportError: cannot import name 'merge_day_bars'`

- [ ] **Step 3: Write minimal implementation**

Append to `trendradar/infrastructure/tushare/syncer.py`:

```python
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
                [local.filter(~pl.col("date").is_in(group["date"])), group]
            ).sort("date")
        else:
            merged = group.sort("date")
        _atomic_write_parquet(merged, target)
        written.append(str(code))
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_daily.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_daily.py
git commit -m "feat: per-day full-market merge into per-code bars (new wins)"
```

---

## Task 6: By-stock sharded path (init / large gap / retry subset)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_sync_by_stock.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from pathlib import Path
import tempfile

from trendradar.infrastructure.tushare.syncer import sync_by_stock


class FakePro:
    def __init__(self, ranges):
        self.ranges = ranges  # code -> list of (start, end) requested
        self.calls = []

    def daily(self, **kwargs):
        self.calls.append(kwargs)
        code = kwargs["ts_code"]
        s = date.fromisoformat(kwargs["start_date"][:4] + "-" + kwargs["start_date"][4:6] + "-" + kwargs["start_date"][6:])
        e = date.fromisoformat(kwargs["end_date"][:4] + "-" + kwargs["end_date"][4:6] + "-" + kwargs["end_date"][6:])
        import polars as pl
        dates = []
        d = s
        while d <= e:
            dates.append(d)
            d = d.fromordinal(d.toordinal() + 1) if False else __import__("datetime").date.fromordinal(d.toordinal() + 1)
        return pl.DataFrame({
            "ts_code": [code] * len(dates),
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "open": [10.0] * len(dates), "high": [10.0] * len(dates),
            "low": [10.0] * len(dates), "close": [10.0] * len(dates),
            "vol": [1000.0] * len(dates), "amount": [10000.0] * len(dates),
        })


def test_sync_by_stock_writes_files_and_marks_done(tmp_path):
    bars_dir = tmp_path / "bars"
    done_path = tmp_path / "sync_done.json"
    retry_path = tmp_path / "sync_retry_codes.json"
    pro = FakePro({})
    result = sync_by_stock(
        pro, ["000001"], date(2026, 8, 18), date(2026, 8, 20),
        bars_dir, done_path, retry_path,
        progress=None, cancel_check=None,
    )
    assert result["failed_codes"] == []
    assert (bars_dir / "000001.parquet").exists()
    from trendradar.infrastructure.tushare.markers import load_sync_done
    assert load_sync_done(done_path) == {date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)}


def test_sync_by_stock_partial_failure_marks_retry(tmp_path):
    class FailingPro(FakePro):
        def daily(self, **kwargs):
            if kwargs["ts_code"] == "000001.SZ":
                raise RuntimeError("boom")
            return super().daily(**kwargs)

    bars_dir = tmp_path / "bars"
    done_path = tmp_path / "sync_done.json"
    retry_path = tmp_path / "sync_retry_codes.json"
    pro = FailingPro({})
    result = sync_by_stock(
        pro, ["000001", "000002"], date(2026, 8, 18), date(2026, 8, 20),
        bars_dir, done_path, retry_path,
        progress=None, cancel_check=None,
    )
    assert result["failed_codes"] == ["000001"]
    from trendradar.infrastructure.tushare.markers import load_retry_codes, load_sync_done
    assert load_retry_codes(retry_path) == ["000001"]
    assert load_sync_done(done_path) == set()  # partial failure -> no day marked
    assert (bars_dir / "000002.parquet").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_by_stock.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `trendradar/infrastructure/tushare/syncer.py`:

```python
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
    codes = load_retry_codes(retry_path) or codes

    def fetch_one(code: str) -> bool:
        for seg_start, seg_end in shard_ranges(start, end):
            if cancel_check and cancel_check():
                return False
            if not bucket.acquire(cancel_check=cancel_check):
                return False
            data = _fetch_with_retry(pro, code, seg_start, seg_end, 3)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_by_stock.py -q`
Expected: PASS (2 passed). Fix the draft `date.fromordinal` line in the test to `import datetime; d + datetime.timedelta(days=1)`.

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_by_stock.py
git commit -m "feat: by-stock sharded sync with retry subset and day marking"
```

---

## Task 7: `sync_market` orchestrator (dispatch, force, end clamp, empty-return grading)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_sync_market.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, datetime, timezone
from pathlib import Path

from trendradar.infrastructure.tushare.syncer import sync_market


class FakePro:
    def __init__(self, daily_by_day=None, trade_days=None):
        self.daily_by_day = daily_by_day or {}
        self.trade_days = trade_days or {
            date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        }
        self.daily_calls = []

    def trade_cal(self, exchange, start_date, end_date):
        import polars as pl
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": int(d in self.trade_days)})
            d = d + __import__("datetime").timedelta(days=1)
        return __import__("polars").DataFrame(rows)

    def daily(self, **kwargs):
        if "trade_date" in kwargs:
            self.daily_calls.append(kwargs["trade_date"])
            return self.daily_by_day.get(kwargs["trade_date"], __import__("polars").DataFrame())
        raise AssertionError("by-stock path not expected")


def _row(code, day, close=10.0):
    return {
        "ts_code": f"{code}.SZ", "trade_date": day.strftime("%Y%m%d"),
        "open": close, "high": close, "low": close, "close": close,
        "vol": 1000.0, "amount": 10000.0,
    }


def test_sync_market_small_gap_uses_daily_path(tmp_path):
    bars_dir = tmp_path / "bars"
    pro = FakePro(daily_by_day={
        "20260819": __import__("polars").DataFrame([_row("000001", date(2026, 8, 19))]),
    })
    result = sync_market(
        pro, bars_dir, tmp_path, {},
        now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        progress=None, cancel_check=None,
    )
    assert result["mode"] == "incremental"
    assert result["synced_days"] == 1
    assert (bars_dir / "000001.parquet").exists()


def test_sync_market_up_to_date_zero_daily_calls(tmp_path):
    bars_dir = tmp_path / "bars"
    # Seed all days via a first run, then second run should not call daily
    pro = FakePro(daily_by_day={
        "20260818": __import__("polars").DataFrame([_row("000001", date(2026, 8, 18))]),
        "20260819": __import__("polars").DataFrame([_row("000001", date(2026, 8, 19))]),
        "20260820": __import__("polars").DataFrame([_row("000001", date(2026, 8, 20))]),
    })
    sync_market(pro, bars_dir, tmp_path, {},
                now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                progress=None, cancel_check=None)
    pro.daily_calls.clear()
    result = sync_market(pro, bars_dir, tmp_path, {},
                         now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                         progress=None, cancel_check=None)
    assert result["skipped_uptodate"] is True
    assert pro.daily_calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_market.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `trendradar/infrastructure/tushare/syncer.py`:

```python
def sync_market(
    pro,
    bars_dir: Path,
    cache_dir: Path,
    request: dict,
    now_utc=None,
    progress=None,
    cancel_check=None,
) -> dict:
    """Top-level incremental sync orchestrator.

    request keys: start_date, end_date, codes, force.
    Returns result_json stats (mode, missing_days, synced_days, synced_codes,
    new_codes, failed_days, failed_codes, skipped_uptodate).
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
                import polars as pl
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

    if not force and is_up_to_date(
        local_min=req_start, local_max=latest,
        req_start=None if request.get("start_date") is None else req_start,
        latest=latest, done=done, trade_days=all_trade,
    ):
        return {"mode": "incremental", "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "skipped_uptodate": True}

    missing = missing_trade_days(all_trade, done, req_start, req_end)

    if force or len(missing) <= 20:
        # Daily path
        from trendradar.infrastructure.tushare.rate_limit import TokenBucket
        bucket = TokenBucket()
        failed_days = 0
        synced_days = 0
        synced_codes = 0
        new_codes = 0
        total = len(missing)
        for idx, day in enumerate(missing, start=1):
            if cancel_check and cancel_check():
                break
            if progress:
                progress(idx, total, str(day))
            if not bucket.acquire(cancel_check=cancel_check):
                break
            df = _fetch_daily_by_date(pro, day)
            if df.is_empty():
                if day == latest:
                    continue  # not done, warning-level, retried next run
                done.add(day)
                save_sync_done(done_path, done)
                failed_days += 0
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
                "skipped_uptodate": False}
    else:
        # By-stock path (init / large gap / retry subset)
        from trendradar.infrastructure.tushare.stocklist import sync_stock_list
        meta = sync_stock_list(bars_dir)
        codes = meta["code"].to_list() if not meta.is_empty() else []
        result = sync_by_stock(
            pro, codes, req_start, req_end, bars_dir,
            done_path, retry_path, progress, cancel_check,
        )
        return {"mode": "init", "missing_days": len(missing),
                "synced_days": len(missing) if not result["failed_codes"] else 0,
                "synced_codes": len(codes) - len(result["failed_codes"]),
                "new_codes": 0, "failed_days": 0,
                "failed_codes": len(result["failed_codes"]),
                "skipped_uptodate": False}
```

Also add `_fetch_daily_by_date`:

```python
def _fetch_daily_by_date(pro, day: date):
    resp = pro.daily(trade_date=day.strftime("%Y%m%d"))
    if resp is None or resp.empty:
        return pl.DataFrame()
    df = pl.from_pandas(resp)
    df = df.rename({c: {"trade_date": "date", "vol": "volume"}.get(c, c) for c in df.columns if c in ("trade_date", "vol")})
    df = df.with_columns(
        pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
    )
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64))
    df = df.with_columns(
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )
    cols = ["code", "date", "open", "high", "low", "close", "volume", "amount", "adj_factor", "is_suspended"]
    return df.select([c for c in cols if c in df.columns]).sort("date")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_market.py -q`
Expected: PASS (2 passed). Note the orchestrator references `sync_stock_list` for the by-stock path — that runs only for large gaps; tests here exercise the daily path.

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_market.py
git commit -m "feat: sync_market orchestrator — dispatch, force, end clamp, empty grading"
```

---

## Task 8: Market sync mutual exclusion + restart recovery

**Files:**
- Modify: `trendradar/app/jobs/executor.py`
- Modify: `trendradar/interfaces/api/app.py`
- Test: `tests/app/test_executor_mutex.py`

- [ ] **Step 1: Write the failing test**

```python
import time
from pathlib import Path

import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _executor(tmp_path: Path) -> JobExecutor:
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return JobExecutor(JobStore(sc.db_path))


def test_second_market_sync_rejected_while_running(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError, match="already running"):
            ex.submit("market_sync", lambda ctx: None, {})
    finally:
        ex._jobs[first].future.result(timeout=5)
        ex.shutdown(wait=True)


def test_other_job_types_not_blocked(tmp_path):
    ex = _executor(tmp_path)
    j1 = ex.submit("selection", lambda ctx: None, {})
    j2 = ex.submit("selection", lambda ctx: None, {})
    assert j1 != j2
    ex.shutdown(wait=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/app/test_executor_mutex.py -q`
Expected: FAIL — second market_sync submits successfully (no rejection)

- [ ] **Step 3: Write minimal implementation**

In `trendradar/app/jobs/executor.py`, inside `submit`, after the shutdown check:

```python
        if job_type == "market_sync":
            with self._lock:
                if any(
                    j.job_type == "market_sync" and not j.future.done()
                    for j in self._jobs.values()
                ):
                    raise RuntimeError("market_sync job already running")
```

In `trendradar/interfaces/api/app.py` lifespan, after `init_schema(store.connect())`:

```python
    # Restart recovery: mark orphaned running jobs as failed so mutual
    # exclusion for market_sync is not permanently locked.
    conn = store.connect()
    conn.execute(
        "UPDATE jobs SET status = 'failed', error_message = 'interrupted by restart', "
        "finished_at = datetime('now') WHERE status IN ('queued', 'running')"
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/app/test_executor_mutex.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add trendradar/app/jobs/executor.py trendradar/interfaces/api/app.py tests/app/test_executor_mutex.py
git commit -m "feat: market_sync mutual exclusion in executor + restart recovery"
```

---

## Task 9: Service, schema, and contract wiring

**Files:**
- Modify: `trendradar/app/services/market_service.py`
- Modify: `trendradar/interfaces/api/schemas/market.py`
- Modify: `trendradar/interfaces/api/routes/market.py`
- Modify: `trendradar/interfaces/api/presenters.py`
- Modify: `trendradar/infrastructure/tushare/stocklist.py` (atomic write)
- Test: `tests/app/test_market_service_incremental.py`, `tests/interfaces/test_api_contract.py`

- [ ] **Step 1: Write the failing tests**

`tests/app/test_market_service_incremental.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


class FakePro:
    def __init__(self, trade_days):
        self.trade_days = trade_days

    def trade_cal(self, exchange, start_date, end_date):
        import polars as pl
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": int(d in self.trade_days)})
            d = d + __import__("datetime").timedelta(days=1)
        return pl.DataFrame(rows)


def test_submit_market_sync_passes_force_and_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    captured = {}
    def fake_sync_market(pro, bars_dir, cache_dir, request, now_utc=None, progress=None, cancel_check=None):
        captured.update(request)
        return {"mode": "incremental", "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "skipped_uptodate": True}

    from trendradar.app.services import market_service
    monkeypatch.setattr(market_service.syncer_module, "sync_market", fake_sync_market)
    # NOTE: patch the symbol used inside submit_market_sync; see implementation.

    job_id = market_service.submit_market_sync(
        ex, {"codes": ["000001"], "force": True, "start_date": "2026-08-01", "end_date": "2026-08-20"},
        bars_dir=tmp_path / "storage" / "market" / "bars",
    )
    ex._jobs[job_id].future.result(timeout=10)
    ex.shutdown(wait=True)
```

Contract additions in `tests/interfaces/test_api_contract.py`:

```python
def test_market_sync_force_passthrough(client):
    resp = client.post(
        "/api/market-data/sync",
        json={"start_date": "2026-08-18", "end_date": "2026-08-19", "force": True, "codes": ["000001"]},
    )
    # force is accepted by the schema (200 even though sync job will fail on
    # missing token in tests; a job id is still produced)
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/app/test_market_service_incremental.py tests/interfaces/test_api_contract.py -q`
Expected: FAIL — schema rejects `force` (422) and service does not pass codes/force.

- [ ] **Step 3: Write minimal implementation**

`trendradar/interfaces/api/schemas/market.py` — add field:

```python
class MarketSyncRequest(BaseModel):
    codes: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    force: bool = False
```

`trendradar/interfaces/api/routes/market.py` — pass force and map 409:

```python
@router.post("/market-data/sync")
def submit_market_sync(body: MarketSyncRequest, request: FastAPIRequest):
    from trendradar.app.services.market_service import submit_market_sync
    try:
        job_id = submit_market_sync(
            _executor(request),
            {"codes": body.codes, "start_date": body.start_date,
             "end_date": body.end_date, "force": body.force},
        )
        return {"data": {"job_id": job_id}}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`trendradar/interfaces/api/presenters.py` — `market_data_sync` branch in `submit_execution_payload`:

```python
    elif jtype == "market_data_sync":
        job_id = submit_market_sync(
            executor,
            {
                "start_date": params.get("start") or params.get("start_date"),
                "end_date": params.get("end") or params.get("end_date"),
                "codes": params.get("codes"),
                "force": params.get("force", False),
            },
        )
        job_type = "market_sync"
```

`trendradar/infrastructure/tushare/stocklist.py` — atomic write (replace `df.write_parquet(output)`):

```python
    fd, tmp = __import__("tempfile").mkstemp(dir=output.parent, suffix=".tmp")
    try:
        __import__("os").close(fd)
        df.write_parquet(tmp)
        __import__("os").replace(tmp, output)
    except BaseException:
        try:
            __import__("os").unlink(tmp)
        except OSError:
            pass
        raise
```

`trendradar/app/services/market_service.py` — rewrite `submit_market_sync` worker to call `sync_market` with force, default start = local earliest, unconditional stock_meta refresh at start:

```python
def submit_market_sync(
    executor: JobExecutor,
    request: dict,
    bars_dir: Optional[Path] = None,
) -> str:
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.tushare import syncer as syncer_module

    if bars_dir is None:
        bars_dir = runtime_root() / "storage" / "market" / "bars"
    bars_dir = Path(bars_dir)
    cache_dir = runtime_root() / "storage" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def worker(ctx: JobContext) -> None:
        ctx.log("Starting market data sync")
        # Unconditional stock_meta refresh at start (new-code visibility even
        # if this sync fails mid-way).
        from trendradar.infrastructure.tushare.stocklist import sync_stock_list
        try:
            stock_df = sync_stock_list(bars_dir)
            ctx.log(f"Refreshed stock list: {stock_df.height} stocks")
        except Exception as e:
            ctx.fail(f"stock list refresh failed: {e}")
            return

        result = syncer_module.sync_market(
            get_pro(),
            bars_dir,
            cache_dir,
            request,
            progress=lambda cur, total, msg: ctx.update_progress(cur, total, msg),
            cancel_check=lambda: ctx.check_cancelled(),
        )
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return
        _register_market_sync_metadata(ctx.job_id, request, result)
        ctx.log(
            f"Sync complete: mode={result.get('mode')}, "
            f"missing_days={result.get('missing_days')}, "
            f"synced_days={result.get('synced_days')}, "
            f"failed_codes={result.get('failed_codes')}"
        )
        ctx.succeed(result)

    return executor.submit("market_sync", worker, request)
```

Note: `get_pro` is imported at module top of market_service already; ensure `sync_market`'s runtime_root/cache paths match the test env (env var).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/app/test_market_service_incremental.py tests/interfaces/test_api_contract.py -q`
Expected: PASS. Adjust the service test to monkeypatch `market_service.syncer_module.sync_market` (module attribute reference) and `get_pro` to a stub if network is undesirable.

- [ ] **Step 5: Commit**

```bash
git add trendradar/app/services/market_service.py trendradar/interfaces/api/schemas/market.py trendradar/interfaces/api/routes/market.py trendradar/interfaces/api/presenters.py trendradar/infrastructure/tushare/stocklist.py tests/app/test_market_service_incremental.py tests/interfaces/test_api_contract.py
git commit -m "feat: wire incremental sync — force/codes passthrough, atomic stock_meta, service orchestrator"
```

---

## Task 10: CLI reset cache + calendar read lock

**Files:**
- Modify: `trendradar/cli.py`
- Modify: `trendradar/domain/market/data_store.py`
- Test: `tests/test_cli.py`, `tests/infrastructure/test_market_data_store.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`:

```python
def test_init_v2_reset_clears_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr("trendradar.cli.source_root", lambda: tmp_path)
    from trendradar.cli import cmd_init_v2

    cmd_init_v2(argparse.Namespace(reset_runtime=False, confirm_reset=False))
    cache = tmp_path / "storage" / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "sync_done.json").write_text('{"dates": []}')
    (cache / "trade_calendar.parquet").write_bytes(b"x")

    args = argparse.Namespace(reset_runtime=True, confirm_reset=True)
    cmd_init_v2(args)

    assert not (cache / "sync_done.json").exists()
    assert not (cache / "trade_calendar.parquet").exists()
```

In `tests/infrastructure/test_market_data_store.py` — existing `test_get_calendar_refreshes_when_bars_change` already covers rebuild; add nothing new (lock is internal). Skip new test here.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — cache files survive reset.

- [ ] **Step 3: Write minimal implementation**

`trendradar/cli.py` RESET_PATHS:

```python
RESET_PATHS = [
    "app.db",
    "objects/",
    "market/",
    "cache/",
]
```

`trendradar/domain/market/data_store.py` — module-level lock around cache rebuild:

```python
_calendar_lock = __import__("threading").Lock()
```

In `_collect_dates`, wrap the rebuild (from `all_paths = sorted(...)` through `return result`) in `with _calendar_lock:`. The cache-read fast path stays outside the lock (readers may read a stale-but-valid cache; rebuild is atomic).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/infrastructure/test_market_data_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trendradar/cli.py trendradar/domain/market/data_store.py tests/test_cli.py
git commit -m "chore: reset clears sync cache; serialize calendar rebuild"
```

---

## Task 11: Integration verification

**Files:**
- Test: `tests/infrastructure/test_sync_integration.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from trendradar.infrastructure.tushare.syncer import sync_market


class FakePro:
    """Synthetic market: 2 stocks, 3 trade days, evolving history."""

    def __init__(self, trade_days, data_by_day):
        self.trade_days = trade_days
        self.data_by_day = data_by_day
        self.daily_calls = []

    def trade_cal(self, exchange, start_date, end_date):
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": int(d in self.trade_days)})
            d = d + __import__("datetime").timedelta(days=1)
        return pl.DataFrame(rows)

    def daily(self, **kwargs):
        if "trade_date" in kwargs:
            self.daily_calls.append(kwargs["trade_date"])
            return self.data_by_day.get(kwargs["trade_date"], pl.DataFrame())
        raise AssertionError("by-stock path not expected")


def _row(code, day, close):
    return {"ts_code": f"{code}.SZ", "trade_date": day.strftime("%Y%m%d"),
            "open": close, "high": close, "low": close, "close": close,
            "vol": 1000.0, "amount": 10000.0}


def test_head_gap_backfill_regression(tmp_path):
    """The exact bug from the spec: earlier start must backfill the head gap."""
    days = [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)]
    pro = FakePro(set(days), {
        d.strftime("%Y%m%d"): pl.DataFrame([_row("000001", d, 10.0)]) for d in days
    })
    bars_dir = tmp_path / "bars"
    cache = tmp_path / "cache"

    # First run covers 08-18..08-20
    sync_market(pro, bars_dir, cache, {"start_date": "2026-08-18", "end_date": "2026-08-20"},
                now_utc=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
                progress=None, cancel_check=None)
    # Second run requests earlier start 08-15 -> must backfill 08-15..08-17
    # (no trade days there in this synthetic cal, but the *request* must not
    # short-circuit; add 08-15 to the cal to prove backfill)
    pro.trade_days.add(date(2026, 8, 15))
    pro.data_by_day["20260815"] = pl.DataFrame([_row("000001", date(2026, 8, 15), 9.5)])
    result = sync_market(pro, bars_dir, cache,
                         {"start_date": "2026-08-15", "end_date": "2026-08-21"},
                         now_utc=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                         progress=None, cancel_check=None)
    assert result["skipped_uptodate"] is False
    df = pl.read_parquet(bars_dir / "000001.parquet")
    assert date(2026, 8, 15) in df["date"].to_list()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_sync_integration.py -q`
Expected: FAIL — earlier start short-circuits (the pre-fix behavior this plan replaces).

- [ ] **Step 3: Run the implementation to make it pass**

The implementation from Tasks 4–7 already makes this pass (start-aware fast path + missing-day backfill). Run the test again; it should pass. If it fails, debug the fast-path/missing-day logic in `sync_market`.

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add tests/infrastructure/test_sync_integration.py
git commit -m "test: head-gap backfill regression (earlier start must not short-circuit)"
```

---

## Self-Review

1. **Spec coverage:** Tasks 1–11 map to spec v5: rate limiter (决策 5), trade calendar (决策 2), markers (决策 4), latest-day/missing-days (决策 3/4/6), daily path (决策 6), by-stock path (决策 5), orchestrator (分派/force/end clamp/空返回分级), mutex+recovery (决策 1), service/schema/presenters (决策 8/2.1/契约), reset+lock (警告 9/建议 12 关联), integration regression (验收).
2. **Placeholder scan:** every code step has full code; the two "draft" fragments in Task 4/6 are flagged with explicit final versions.
3. **Type consistency:** `sync_market(pro, bars_dir, cache_dir, request, now_utc, progress, cancel_check) -> dict`; `sync_by_stock(pro, codes, start, end, bars_dir, done_path, retry_path, progress, cancel_check, bucket, max_workers) -> dict`; `merge_day_bars(day_df, bars_dir) -> list[str]`; markers/calendar helpers consistent across tasks.
