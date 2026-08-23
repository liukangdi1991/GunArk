# Market Sync Mode Announcement & Failed-Code Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make market sync communicate its mode (full/incremental) at start with the reason, rename internal `mode=init` to `mode=full`, and auto-retry failed codes (up to 9 rounds, 30s apart, only failed subset) after the main sync, plus fix the rate-limit root cause (retries bypass token bucket).

**Architecture:** Pure `decide_mode()` decision + `plan_sync()` loading wrapper (single decision source consumed by `sync_market`); retry loop orchestrates `sync_by_stock` over the failed subset; `bucket.acquire()` moves into `_fetch_with_retry` so retries consume tokens; rate-limit errors abandon the attempt to the retry loop instead of hot-retrying.

**Tech Stack:** Python 3.11+, polars, ThreadPoolExecutor (existing).

**Spec:** `docs/superpowers/specs/2026-08-23-market-sync-mode-and-retry-design.md` (4 review rounds, clean)

**Baseline (must stay green):** `cd /root/workspace/repo/GunArk && python3 -m pytest -q -p no:cacheprovider` → 258 passed.

---

### Task 1: `decide_mode` pure function (TDD)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py` (add function near top, after `IP_BAN_ERROR_MSG`)
- Create: `tests/infrastructure/test_sync_plan.py`
- Test: the new file

- [ ] **Step 1: Write the failing test**

```python
"""decide_mode: full vs incremental decision (pure, no I/O)."""
from trendradar.infrastructure.tushare.syncer import decide_mode


def test_retry_codes_force_full():
    assert decide_mode(missing_days=1, force=False, retry_codes=["000001"]) == "full"


def test_force_always_full():
    assert decide_mode(missing_days=0, force=True, retry_codes=[]) == "full"


def test_large_gap_is_full():
    assert decide_mode(missing_days=21, force=False, retry_codes=[]) == "full"


def test_small_gap_is_incremental():
    assert decide_mode(missing_days=20, force=False, retry_codes=[]) == "incremental"


def test_zero_gap_is_incremental():
    assert decide_mode(missing_days=0, force=False, retry_codes=[]) == "incremental"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_sync_plan.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'decide_mode'`

- [ ] **Step 3: Write minimal implementation**

Add to `trendradar/infrastructure/tushare/syncer.py` (after `IP_BAN_ERROR_MSG`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_sync_plan.py -q -p no:cacheprovider`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_plan.py
git commit -m "feat: decide_mode pure function for sync mode selection"
```

### Task 2: `plan_sync` loading wrapper + `SyncPlan`

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_sync_plan.py`

- [ ] **Step 1: Write the failing test**

```python
import polars as pl
from datetime import date, datetime, timezone
from pathlib import Path
from trendradar.infrastructure.tushare.syncer import plan_sync


class FakePro:
    def __init__(self, trade_days):
        self._days = trade_days
    def trade_cal(self, **kwargs):
        import pandas as pd
        return pd.DataFrame({"cal_date": [d.strftime("%Y%m%d") for d in self._days],
                             "is_open": [1] * len(self._days)})


def _run_plan(tmp_path, trade_days, request):
    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    return plan_sync(FakePro(trade_days), bars, cache, request,
                     now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc))


def test_plan_large_gap_is_full(tmp_path):
    # 25 missing trade days (>20) → full mode
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(25)]
    p = _run_plan(tmp_path, days, {"start_date": days[0].isoformat(), "end_date": days[-1].isoformat()})
    assert p.mode == "full"
    assert p.missing_days == 25
    assert p.uptodate is False


def test_plan_uptodate_when_done(tmp_path):
    days = [date(2026, 8, 3), date(2026, 8, 4)]
    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    (cache).mkdir(parents=True)
    import json
    (cache / "sync_done.json").write_text(json.dumps({"dates": ["2026-08-03", "2026-08-04"]}))
    p = plan_sync(FakePro(days), bars, cache,
                  {"start_date": "2026-08-03", "end_date": "2026-08-04"},
                  now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc))
    assert p.uptodate is True
    assert p.missing_days == 0


class FailingCalendarPro:
    def trade_cal(self, **kwargs):
        raise Exception("network down")


def test_plan_raises_when_calendar_unavailable(tmp_path):
    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    import pytest
    with pytest.raises(RuntimeError, match="trade calendar unavailable"):
        plan_sync(FailingCalendarPro(), bars, cache,
                  {"start_date": "2026-08-03", "end_date": "2026-08-05"})

```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_sync_plan.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'plan_sync'`

- [ ] **Step 3: Implement `SyncPlan` + `plan_sync`**

Add to `syncer.py` (imports `dataclass`, `date`, `Path` already present; `field` if needed):

```python
@dataclass(frozen=True)
class SyncPlan:
    mode: str
    missing_days: int
    missing_dates: list          # list[date], execution context for daily path
    start: date
    end: date
    latest: date
    all_trade: set               # set[date]
    done: set                    # set[date]
    uptodate: bool
    force: bool
    retry_codes: list            # list[str] from sync_retry_codes.json
```

`plan_sync` moves the loading logic currently at the head of `sync_market` (lines ~391-437: now/today, req_start/req_end resolution, calendar fetch with cache fallback, `latest_tradeable_day`, req_end clamp, `load_sync_done` + legacy first-run cross-check, `is_up_to_date`, `missing_trade_days`) and computes the mode via `decide_mode`. Reference the existing code verbatim when moving; do not reimplement. Signature:

```python
def plan_sync(pro, bars_dir: Path, cache_dir: Path, request: dict,
              now_utc=None) -> SyncPlan:
    ...
```

Returns a SyncPlan; on calendar fetch failure with empty cache, raises `RuntimeError("trade calendar unavailable (fetch failed and no cache)")` (existing behavior).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_sync_plan.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Run existing sync tests to confirm no regression (plan_sync not yet wired)**

Run: `python3 -m pytest tests/infrastructure/test_sync_market.py tests/infrastructure/test_sync_integration.py -q -p no:cacheprovider`
Expected: PASS (sync_market unchanged this task)

- [ ] **Step 6: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_plan.py
git commit -m "feat: plan_sync loading wrapper and SyncPlan (single decision source)"
```

### Task 3: Wire `sync_market` to consume the plan; rename init→full; `retry_rounds` field

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_sync_market.py`, `tests/infrastructure/test_sync_integration.py`, `tests/app/test_execution_registration.py`, `tests/app/test_market_service_incremental.py`

- [ ] **Step 1: Update `sync_market` signature and head**

```python
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
    if plan is None:
        plan = plan_sync(pro, bars_dir, cache_dir, request, now_utc=now_utc)
    if plan.uptodate:
        return {"mode": plan.mode, "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": True}
```

Delete the old head loading block (now in `plan_sync`). Replace uses of `missing` → `plan.missing_dates`, `done` → `plan.done`, `latest` → `plan.latest`, `force`/`req_start`/`req_end` → `plan.force`/`plan.start`/`plan.end` inside the two branches.

- [ ] **Step 2: Update the two branch result dicts**

Daily branch (`mode="incremental"`): add `"retry_rounds": 0` before `"skipped_uptodate"`.

By-stock branch: rename `"mode": "init"` → `"mode": "full"`, replace the misleading `"synced_days": len(missing) if not result["failed_codes"] else 0` with `"synced_days": 0`, add `"retry_rounds": 0`.

- [ ] **Step 3: Run tests to verify behavior preserved (mode value not asserted anywhere)**

Run: `python3 -m pytest tests/infrastructure/test_sync_market.py tests/infrastructure/test_sync_integration.py tests/infrastructure/test_sync_by_stock.py -q -p no:cacheprovider`
Expected: PASS. If any test asserts `"mode" == "init"` (none known), update it to `"full"`.

- [ ] **Step 4: Update app-test mocks to include `retry_rounds`**

`tests/app/test_execution_registration.py:188` and `tests/app/test_market_service_incremental.py:32`: add `"retry_rounds": 0` to the mocked sync result dicts.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q -p no:cacheprovider`
Expected: 258 passed

- [ ] **Step 6: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/
git commit -m "refactor: sync_market consumes SyncPlan; mode init→full; retry_rounds field"
```

### Task 4: Rate-limit fix — retries consume tokens; rate-limit errors abandon

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py`
- Test: `tests/infrastructure/test_tushare_syncer.py`

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock, patch
import polars as pl
from trendradar.infrastructure.tushare.syncer import _fetch_with_retry
from trendradar.infrastructure.tushare.rate_limit import TokenBucket


def test_retry_consumes_bucket_tokens():
    pro = MagicMock()
    pro.daily.side_effect = [Exception("boom"), None]  # first attempt fails
    bucket = TokenBucket(rate_per_min=1, burst=1)       # 1 token only
    with patch("trendradar.infrastructure.tushare.syncer.time.sleep"):
        _fetch_with_retry(pro, "000001", __import__("datetime").date(2026,1,1),
                          __import__("datetime").date(2026,1,31), 3, bucket=bucket)
    assert pro.daily.call_count == 1  # second attempt starved: no token left


def test_rate_limit_error_abandons_without_retry():
    pro = MagicMock()
    pro.daily.side_effect = [Exception("抱歉，您访问接口(daily)频率超限(300次/分钟)")]
    with patch("trendradar.infrastructure.tushare.syncer.time.sleep"):
        result = _fetch_with_retry(pro, "000001", __import__("datetime").date(2026,1,1),
                                   __import__("datetime").date(2026,1,31), 3)
    assert result is None
    assert pro.daily.call_count == 1  # no hot retry on rate-limit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_tushare_syncer.py -q -p no:cacheprovider`
Expected: FAIL (current code retries both cases)

- [ ] **Step 3: Implement**

```python
RATE_LIMIT_MSG = "频率超限"  # matches Tushare "访问接口(daily)频率超限(300次/分钟)"

def _fetch_with_retry(pro, code, start, end, max_retries, bucket=None,
                      cancel_check=None) -> Optional[pl.DataFrame]:
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    params = {"ts_code": _to_ts_code(code), "start_date": start_s,
              "end_date": end_s, "freq": "D"}
    for attempt in range(max_retries):
        if bucket is not None:
            if not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
                return None  # starved/timeout → failed code, retry loop handles it
        try:
            resp = pro.daily(**params)
            return _response_to_df(resp, code)
        except Exception as e:
            msg = str(e)
            if RATE_LIMIT_MSG in msg:
                # Per-window limit (300/min): abandon this code to the retry
                # loop; hot-retrying here only adds load into the same window.
                logger.warning("Rate limit on %s: %s", code, msg)
                return None
            if IP_BAN_ERROR_MSG in msg:
                logger.warning("Rate limit hit on %s attempt %d/%d, cooling 600s",
                               code, attempt + 1, max_retries)
                time.sleep(600)
                continue
            backoff = 2 ** attempt
            logger.warning("Fetch error for %s attempt %d/%d: %s, retry in %ds",
                           code, attempt + 1, max_retries, msg, backoff)
            time.sleep(backoff)
    logger.error("All %d retries exhausted for %s", max_retries, code)
    return None
```

- [ ] **Step 4: Update `sync_by_stock.fetch_one` to drop its own acquire**

Remove the `if not bucket.acquire(cancel_check=cancel_check): return False` line from `fetch_one`; pass `bucket=bucket, cancel_check=cancel_check` into `_fetch_with_retry` instead. `sync_kline` callers pass no bucket (unchanged legacy behavior).

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/infrastructure/test_tushare_syncer.py tests/infrastructure/test_sync_by_stock.py -q -p no:cacheprovider`
Expected: PASS. Then full suite: `python3 -m pytest -q -p no:cacheprovider` → 258 passed.

- [ ] **Step 6: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_tushare_syncer.py
git commit -m "fix: retries consume rate-limit tokens; per-window limit abandons to retry loop"
```

### Task 5: Retry loop (≤9 rounds, 30s apart, failed subset only)

**Files:**
- Modify: `trendradar/infrastructure/tushare/syncer.py` (by-stock branch of `sync_market`)
- Test: `tests/infrastructure/test_sync_market.py`

- [ ] **Step 1: Write the failing test**

```python
def test_full_mode_retries_failed_codes(tmp_path, monkeypatch):
    """mock sync_by_stock: round 1 fails 2 codes, round 2 succeeds all."""
    import trendradar.infrastructure.tushare.syncer as syncer_mod
    from datetime import date, datetime, timezone
    bars = tmp_path / "bars"; cache = tmp_path / "cache"
    bars.mkdir(parents=True); cache.mkdir(parents=True)

    real_plan = syncer_mod.plan_sync
    fake = MagicMock()
    calls = {"n": 0}
    def fake_sync(pro, codes, start, end, bars_dir, done_path, retry_path,
                  progress=None, cancel_check=None, bucket=None, max_workers=6):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"failed_codes": ["000002", "000003"]}
        return {"failed_codes": []}
    monkeypatch.setattr(syncer_mod, "sync_by_stock", fake_sync)
    monkeypatch.setattr(syncer_mod, "sync_stock_list",
                        lambda bars_dir: __import__("polars").DataFrame({"code": ["000001", "000002", "000003"]}))

    pro = MagicMock()
    result = syncer_mod.sync_market(pro, bars, cache, {"start_date": "2026-08-01", "end_date": "2026-08-05"},
                                    now_utc=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
                                    retry_interval=0, max_retry_rounds=9)
    assert result["mode"] == "full"
    assert result["failed_codes"] == []
    assert result["retry_rounds"] == 1
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_sync_market.py::test_full_mode_retries_failed_codes -q -p no:cacheprovider`
Expected: FAIL (`retry_rounds` missing / only 1 sync_by_stock call)

- [ ] **Step 3: Implement the retry loop in the by-stock branch**

Replace the tail of the by-stock branch with:

```python
    else:
        # By-stock path (full / large gap / retry subset)
        from trendradar.infrastructure.tushare.stocklist import sync_stock_list
        meta = sync_stock_list(bars_dir)
        codes = meta["code"].to_list() if not meta.is_empty() else []
        result = sync_by_stock(pro, codes, plan.start, plan.end, bars_dir,
                               done_path, retry_path, progress, cancel_check)
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
                                done_path, retry_path, _sub_progress, cancel_check)
            failed = sub["failed_codes"]
            if not failed and progress:
                progress(0, 0, "全部失败代码已补完")
        return {"mode": "full", "missing_days": plan.missing_days,
                "synced_days": 0, "synced_codes": len(codes) - len(failed),
                "new_codes": 0, "failed_days": 0, "failed_codes": len(failed),
                "retry_rounds": retry_rounds, "skipped_uptodate": False}
```

Note: `done_path`/`retry_path`/`bars_dir` come from `plan_sync`'s caller context — keep the existing local path bindings (cal_path/done_path/retry_path computed in `plan_sync` are replaced by recomputing them in `sync_market` from `cache_dir`; move those three path bindings into `sync_market`'s body).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_sync_market.py -q -p no:cacheprovider`
Expected: PASS (new + existing)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest -q -p no:cacheprovider`
Expected: 258 passed

- [ ] **Step 6: Commit**

```bash
git add trendradar/infrastructure/tushare/syncer.py tests/infrastructure/test_sync_market.py
git commit -m "feat: failed-code retry loop in full sync mode (≤9 rounds, 30s, failed subset)"
```

### Task 6: `market_service` mode announcement + uptodate short-circuit + completion log

**Files:**
- Modify: `trendradar/app/services/market_service.py`
- Test: `tests/app/test_market_service_incremental.py`

- [ ] **Step 1: Update the worker**

In `submit_market_sync`'s worker, after the stock-list refresh and before calling `sync_market`:

```python
        from trendradar.infrastructure.tushare.syncer import plan_sync
        plan = plan_sync(get_pro(), bars_dir, cache_dir, request)
        if plan.uptodate:
            ctx.log("行情已是最新，跳过同步")
            ctx.succeed({"mode": plan.mode, "missing_days": 0, "synced_days": 0,
                         "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                         "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": True})
            return
        if plan.mode == "full":
            ctx.log(f"数据缺口 {plan.missing_days} 天 > 20 天，启用全量同步（按股票拉取全历史）")
        else:
            ctx.log(f"数据缺口 {plan.missing_days} 天 ≤ 20 天，启用增量同步（按日拉取）")
        result = syncer_module.sync_market(
            get_pro(), bars_dir, cache_dir, request, plan=plan,
            progress=lambda cur, total, msg: ctx.update_progress(cur, total, msg),
            cancel_check=lambda: ctx.check_cancelled(),
        )
```

Note: `get_pro()` is called once in the worker already — bind it to a local `pro` and reuse for both `plan_sync` and `sync_market`. Update the completion log to include `retry_rounds`:

```python
        ctx.log(
            f"Sync complete: mode={result.get('mode')}, "
            f"missing_days={result.get('missing_days')}, "
            f"synced_days={result.get('synced_days')}, "
            f"synced_codes={result.get('synced_codes')}, "
            f"failed_codes={result.get('failed_codes')}, "
            f"retry_rounds={result.get('retry_rounds')}"
        )
```

- [ ] **Step 2: Update `tests/app/test_market_service_incremental.py`**

The worker now calls `plan_sync` and may short-circuit before `sync_market`. Patch `syncer.plan_sync` to return a real `SyncPlan` constructed directly (from `trendradar.infrastructure.tushare.syncer import SyncPlan`): `SyncPlan(mode="incremental", missing_days=0, missing_dates=[], start=date(2026,8,1), end=date(2026,8,5), latest=date(2026,8,5), all_trade=set(), done=set(), uptodate=False, force=False, retry_codes=[])`. Assertions: with `uptodate=False, mode="incremental"` → `sync_market` called with `plan=`; with `uptodate=True` → `sync_market` NOT called and result `skipped_uptodate=True`. Add `"retry_rounds": 0` to any result mocks.

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/app/test_market_service_incremental.py tests/app/test_execution_registration.py -q -p no:cacheprovider`
Expected: PASS. Then full suite → 258 passed.

- [ ] **Step 4: Commit**

```bash
git add trendradar/app/services/market_service.py tests/app/
git commit -m "feat: sync mode announcement at start; uptodate short-circuit; completion log with retry_rounds"
```

### Task 7: Final verification

- [ ] Full suite: `python3 -m pytest -q -p no:cacheprovider` → 258 passed
- [ ] `git status --porcelain` shows only intended changes
- [ ] Sanity: `python3 -c "import trendradar.infrastructure.tushare.syncer as s; print(s.decide_mode(5, False, []), s.decide_mode(25, False, []))"` → `incremental full`
- [ ] Update spec status line to "已实现" and commit docs.

---

## Sequencing

Tasks 1→2→3 are sequential (plan plumbing). Task 4 (rate-limit) is independent of 3 but shares `syncer.py` — do it after 3 to avoid merge churn. Task 5 depends on 3+4. Task 6 depends on 3. Task 7 last. Every task ends with a green suite; commit after each.

## Spec coverage map

| Spec requirement | Task |
|---|---|
| decide_mode pure decision | 1 |
| plan_sync loading wrapper / single source | 2, 3 |
| mode init→full rename + synced_days=0 | 3 |
| retry_rounds field everywhere | 3, 5 |
| acquire into fetch layer; retries consume tokens | 4 |
| rate-limit error → abandon to retry loop; IP_BAN 600s | 4 |
| retry loop ≤9 rounds, 30s cancellable, failed subset | 5 |
| start announcement + uptodate contract + completion log | 6 |
| test plan (decide_mode, plan_sync, retry loop, rate-limit) | 1, 2, 4, 5 |
