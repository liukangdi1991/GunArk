"""Market data sync orchestration."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.executor import JobExecutor
from trendradar.infrastructure.tushare import syncer as syncer_module
from trendradar.infrastructure.tushare.client import get_pro


def submit_market_sync(
    executor: JobExecutor,
    request: dict,
    bars_dir: Optional[Path] = None,
) -> str:
    """Submit a market data sync job.

    Args:
        executor: JobExecutor instance for running async jobs.
        request: dict with keys:
            - codes: list[str] (optional, defaults to all stocks from stock list)
            - start_date: str (YYYY-MM-DD, optional)
            - end_date: str (YYYY-MM-DD, optional)
            - force: bool (optional, force full re-sync)
        bars_dir: Path to the bars data directory.

    Returns:
        job_id: str
    """
    from trendradar.infrastructure.runtime import runtime_root

    if bars_dir is None:
        bars_dir = runtime_root() / "storage" / "market" / "bars"
    bars_dir = Path(bars_dir)
    cache_dir = runtime_root() / "storage" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def worker(ctx: JobContext) -> None:
        ctx.log("Starting market data sync")
        pro = get_pro()
        # Unconditional stock_meta refresh at start (new-code visibility even
        # if this sync fails mid-way).
        from trendradar.infrastructure.tushare.stocklist import sync_stock_list
        try:
            stock_df = sync_stock_list(bars_dir)
            ctx.log(f"Refreshed stock list: {stock_df.height} stocks")
        except Exception as e:
            ctx.fail(f"stock list refresh failed: {e}")
            return

        from trendradar.infrastructure.tushare.syncer import plan_sync
        plan = plan_sync(pro, bars_dir, cache_dir, request)

        def _complete(result: dict) -> None:
            _register_market_sync_metadata(ctx.job_id, request, result)
            ctx.log(
                f"Sync complete: mode={result.get('mode')}, "
                f"missing_days={result.get('missing_days')}, "
                f"synced_days={result.get('synced_days')}, "
                f"synced_codes={result.get('synced_codes')}, "
                f"failed_codes={result.get('failed_codes')}, "
                f"retry_rounds={result.get('retry_rounds')}"
            )
            ctx.succeed(result)

        if plan.uptodate:
            ctx.log("行情已是最新，跳过同步")
            _complete({"mode": "incremental", "missing_days": 0, "synced_days": 0,
                       "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                       "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": True})
            return
        if plan.mode == "full":
            if plan.retry_codes:
                ctx.log(f"存在 {len(plan.retry_codes)} 个失败代码待补，启用全量同步（按股票补拉）")
            elif plan.force:
                ctx.log("已请求强制全量同步（按股票拉取全历史）")
            else:
                ctx.log(f"数据缺口 {plan.missing_days} 天 > 20 天，启用全量同步（按股票拉取全历史）")
        else:
            ctx.log(f"数据缺口 {plan.missing_days} 天 ≤ 20 天，启用增量同步（按日拉取）")

        result = syncer_module.sync_market(
            pro,
            bars_dir,
            cache_dir,
            request,
            plan=plan,
            progress=lambda cur, total, msg: ctx.update_progress(cur, total, msg),
            cancel_check=lambda: ctx.check_cancelled(),
        )
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return
        _complete(result)

    return executor.submit("market_sync", worker, request)


def _register_market_sync_metadata(
    execution_key: str,
    request: dict,
    result: dict,
) -> None:
    """Register executions + market_sync_runs rows after a completed sync."""
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import register_execution

    codes = request.get("codes") or []
    start_str = request.get("start_date")
    end_str = request.get("end_date")
    start = date.fromisoformat(start_str) if start_str else date.today()
    end = date.fromisoformat(end_str) if end_str else date.today()

    storage_root = runtime_root() / "storage"
    with StorageConnection(storage_root).connection() as conn:
        register_execution(conn, execution_key, "market_sync")
        conn.execute(
            "INSERT OR IGNORE INTO market_sync_runs "
            "(execution_key, start_date, end_date, stock_count, skipped_latest, "
            " empty_count, failed_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                execution_key,
                start.isoformat(),
                end.isoformat(),
                len(codes),
                int(bool(result.get("skipped_uptodate", False))),
                result.get("empty_count", 0),
                result.get("failed_codes", 0),
            ),
        )
        conn.commit()


def get_market_status(store) -> dict:
    """Get current market data status.

    Args:
        store: StorageConnection instance.

    Returns:
        dict with keys: latest_date, stock_count, latest_sync_run.
    """
    from trendradar.infrastructure.runtime import runtime_root

    bars_dir = runtime_root() / "storage" / "market" / "bars"
    stock_count = len(list(bars_dir.glob("*.parquet"))) if bars_dir.exists() else 0

    conn = store.connect()
    latest_sync = conn.execute(
        "SELECT * FROM market_sync_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    latest_date = None
    if bars_dir.exists():
        all_dates = set()
        for p in bars_dir.glob("*.parquet"):
            try:
                import polars as pl
                df = pl.read_parquet(p, columns=["date"])
                if not df.is_empty():
                    max_d = df["date"].max()
                    if max_d is not None:
                        all_dates.add(max_d)
            except Exception:
                continue
        if all_dates:
            latest_date = str(max(all_dates))

    return {
        "latest_date": latest_date,
        "stock_count": stock_count,
        "latest_sync_run": dict(latest_sync) if latest_sync else None,
    }
