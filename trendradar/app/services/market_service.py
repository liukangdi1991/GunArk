"""市场数据同步门面：执行入口 + 确认入账同步 API（spec §3.7/§3.8/§6）。"""

from __future__ import annotations

from trendradar.app.jobs.executor import JobExecutor


def submit_market_bars_sync(executor: JobExecutor, request: dict) -> str:
    from trendradar.app.services.market_sync.service import bars_sync_worker

    return executor.submit(
        "market_bars_sync",
        lambda ctx: bars_sync_worker(ctx, request),
        request,
    )


def submit_market_backfill_codes(executor: JobExecutor, request: dict) -> str:
    """通道二"立即补齐"：先清 attempts（误判后重新给 3 次机会，spec §3.7）。"""
    from trendradar.app.services.market_sync.service import backfill_codes_worker
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore

    SyncStore(storage_root()).reset_skip_attempts()
    return executor.submit(
        "market_backfill_codes",
        lambda ctx: backfill_codes_worker(ctx, request),
        request,
    )


def confirm_doubtful_days() -> dict:
    """"确认入账"同步 API（非作业，spec §3.8）：all-or-nothing。"""
    from trendradar.app.services.market_sync.service import _invalidate_status_cache
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from trendradar.infrastructure.tushare.writer import readback_calendar

    root = storage_root()
    store = SyncStore(root)
    doubtful = store.doubtful_days()
    if not doubtful:
        return {"status": "noop", "confirmed": []}
    readback = readback_calendar(root / "market" / "bars")
    missing = [d for d in doubtful if d not in readback]
    if missing:
        raise ValueError(
            f"以下日期不在盘上，请先重拉: {[d.isoformat() for d in missing]}"
        )
    store.add_done_days(doubtful)
    store.set_doubtful_days([])
    _invalidate_status_cache()
    return {"status": "ok", "confirmed": [d.isoformat() for d in doubtful]}
