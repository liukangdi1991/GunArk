"""Market data sync orchestration."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.executor import JobExecutor
from trendradar.infrastructure.tushare.syncer import sync_kline
from trendradar.infrastructure.tushare.stocklist import sync_stock_list


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
            - start_date: str (YYYY-MM-DD, optional, defaults to 90 days ago)
            - end_date: str (YYYY-MM-DD, optional, defaults to today)
        bars_dir: Path to the bars data directory.

    Returns:
        job_id: str
    """
    if bars_dir is None:
        from trendradar.infrastructure.runtime import runtime_root
        bars_dir = runtime_root() / "storage" / "market" / "bars"

    def worker(ctx: JobContext) -> None:
        ctx.log("Starting market data sync")

        codes = request.get("codes")
        if codes is None or len(codes) == 0:
            ctx.log("Fetching stock list from Tushare")
            stock_df = sync_stock_list(bars_dir)
            if stock_df.is_empty():
                ctx.fail("Failed to fetch stock list")
                return
            codes = stock_df["code"].to_list()
            ctx.log(f"Resolved {len(codes)} stocks from stock list")

        start_str = request.get("start_date")
        end_str = request.get("end_date")

        if start_str:
            start = date.fromisoformat(start_str)
        else:
            from datetime import timedelta
            start = date.today() - timedelta(days=90)

        if end_str:
            end = date.fromisoformat(end_str)
        else:
            end = date.today()

        ctx.log(f"Syncing {len(codes)} stocks from {start} to {end}")

        def progress(current: int, total: int, code: str) -> None:
            if ctx.check_cancelled():
                return
            ctx.update_progress(current, total, code)

        def cancel_check() -> bool:
            return ctx.check_cancelled()

        result = sync_kline(
            codes=codes,
            start=start,
            end=end,
            bars_dir=bars_dir,
            progress=progress,
            cancel_check=cancel_check,
        )

        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return

        ctx.log(
            f"Sync complete: synced={result['synced']}, skipped={result['skipped']}, "
            f"failed={result['failed']}, empty={result['empty']}"
        )

        _register_market_sync_metadata(ctx.job_id, codes, start, end, result)

        ctx.succeed(result)

    return executor.submit("market_sync", worker, request)


def _register_market_sync_metadata(
    execution_key: str,
    codes: list[str],
    start: date,
    end: date,
    result: dict,
) -> None:
    """Register executions + market_sync_runs rows after a completed sync."""
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import register_execution

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
                result.get("skipped", 0),
                result.get("empty", 0),
                result.get("failed", 0),
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
