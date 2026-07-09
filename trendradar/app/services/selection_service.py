"""Selection orchestration service."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import polars as pl

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.executor import JobExecutor
from trendradar.domain.market.data_store import MarketDataStore
from trendradar.domain.signal.models import SignalSet, StrategySignal
from trendradar.domain.strategy.protocol import SelectionContext
from trendradar.domain.strategy.registry import get as get_defn, list_all
from trendradar.domain.strategy.resolver import resolve as resolve_strategies


def _get_strategy_resolve_input(store) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Load strategy groups, members, and settings from DB."""
    conn = store.connect()

    group_rows = conn.execute(
        "SELECT id, name, enabled FROM strategy_groups"
    ).fetchall()
    group_defs = [
        {"id": g["id"], "name": g["name"], "enabled": bool(g["enabled"])}
        for g in group_rows
    ]

    member_rows = conn.execute(
        "SELECT group_id, strategy_id, sort_order FROM strategy_group_members"
    ).fetchall()
    member_defs = [
        {"group_id": m["group_id"], "strategy_id": m["strategy_id"], "sort_order": m["sort_order"]}
        for m in member_rows
    ]

    setting_rows = conn.execute(
        "SELECT strategy_id, enabled, params_json FROM strategy_settings"
    ).fetchall()
    settings_map: dict[str, dict] = {}
    for s in setting_rows:
        settings_map[s["strategy_id"]] = {
            "enabled": bool(s["enabled"]),
            "params": json.loads(s.get("params_json", "{}")),
        }

    all_defs = list_all()
    for d in all_defs:
        if d.strategy_id not in settings_map:
            settings_map[d.strategy_id] = {
                "enabled": True,
                "params": dict(d.default_params),
            }

    return group_defs, member_defs, settings_map


def _run_selection(
    ctx: JobContext,
    market_store: MarketDataStore,
    request: dict,
    store,
) -> SignalSet:
    """Core selection logic shared by single and batch modes."""
    ctx.log("Resolving strategy groups and settings")
    group_defs, member_defs, settings_map = _get_strategy_resolve_input(store)

    resolved = resolve_strategies(group_defs, member_defs, settings_map, request)
    if not resolved:
        ctx.log("No strategies resolved")
        return SignalSet(execution_key=ctx.job_id)

    ctx.log(f"Resolved {len(resolved)} strategies: {[d.strategy_id for d in resolved]}")

    start_str = request.get("start_date")
    end_str = request.get("end_date")

    if start_str:
        start = date.fromisoformat(start_str)
    else:
        start = date.today() - timedelta(days=90)

    if end_str:
        end = date.fromisoformat(end_str)
    else:
        end = date.today()

    trading_dates = market_store.trading_dates(start, end)
    if not trading_dates:
        ctx.log("No trading dates found in range")
        return SignalSet(execution_key=ctx.job_id)

    ctx.log(f"Processing {len(trading_dates)} trading dates from {trading_dates[0]} to {trading_dates[-1]}")

    max_window = 120
    extended_start = trading_dates[0] - timedelta(days=max_window * 2)
    codes = request.get("codes")
    if codes is None or len(codes) == 0:
        meta = market_store.stock_meta()
        codes = meta["code"].to_list() if not meta.is_empty() else []
        ctx.log(f"Using all {len(codes)} available stocks")
    else:
        ctx.log(f"Using {len(codes)} specified stocks")

    market_data = market_store.load_bars(codes, extended_start, trading_dates[-1])
    if market_data.is_empty():
        ctx.log("No market data loaded")
        return SignalSet(execution_key=ctx.job_id)

    ctx.log(f"Loaded {market_data.height} market data rows")

    all_signals: list[StrategySignal] = []
    total_dates = len(trading_dates)
    strategies_snapshot = [
        {
            "strategy_id": d.strategy_id,
            "name": d.name,
            "params": settings_map.get(d.strategy_id, {}).get("params", {}),
        }
        for d in resolved
    ]

    for date_idx, trade_date in enumerate(trading_dates):
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
            return SignalSet(execution_key=ctx.job_id)

        ctx.update_progress(date_idx + 1, total_dates, str(trade_date))

        day_data = market_data.filter(pl.col("date") == trade_date)
        if day_data.is_empty():
            continue

        day_codes = day_data["code"].unique().to_list()
        candidate_codes = [c for c in codes if c in day_codes]
        if not candidate_codes:
            continue

        for defn in resolved:
            selector = defn.selector_class(defn)
            try:
                context = SelectionContext(
                    trade_date=trade_date,
                    market_data=market_data,
                    candidate_codes=candidate_codes,
                    get_data_dict=lambda codes=candidate_codes: {
                        c: market_data.filter(pl.col("code") == c)
                        for c in codes
                    },
                )
                result = selector.select(context)
                if result.selected_codes:
                    all_signals.append(
                        StrategySignal(
                            strategy_id=result.strategy_id,
                            strategy_name=result.strategy_name,
                            signal_date=result.trade_date,
                            codes=result.selected_codes,
                        )
                    )
            except Exception as e:
                ctx.log(f"Error in strategy {defn.strategy_id} on {trade_date}: {e}", level="WARN")

    ctx.log(f"Selection complete: {len(all_signals)} signals generated")

    return SignalSet(
        execution_key=ctx.job_id,
        signal_from=trading_dates[0],
        signal_to=trading_dates[-1],
        strategies_snapshot=strategies_snapshot,
        signals=all_signals,
    )


def _write_selection_manifest(execution_key: str, signal_set: SignalSet) -> None:
    """Write manifest.json and lineage.json for a selection execution."""
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    from trendradar.infrastructure.runtime import runtime_root

    exec_dir = runtime_root() / "storage" / "objects" / "executions" / execution_key / "selection"
    exec_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "2.0",
        "execution_key": execution_key,
        "execution_type": "selection",
        "created_at": now,
        "status": "success",
    }
    (exec_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lineage = {
        "resolved_strategies": signal_set.strategies_snapshot,
        "market_data": {
            "from": str(signal_set.signal_from) if signal_set.signal_from else None,
            "to": str(signal_set.signal_to) if signal_set.signal_to else None,
        },
    }
    (exec_dir / "lineage.json").write_text(
        json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def submit_selection(
    executor: JobExecutor,
    market_store: MarketDataStore,
    request: dict,
    store=None,
) -> str:
    """Submit a single-run selection job.

    Args:
        executor: JobExecutor instance.
        market_store: MarketDataStore instance for loading bars.
        request: dict with optional keys: groups, strategies, start_date, end_date, codes.
        store: StorageConnection instance for resolving strategy settings.

    Returns:
        job_id: str
    """
    def worker(ctx: JobContext) -> None:
        ctx.log("Starting selection job")

        signal_set = _run_selection(ctx, market_store, request, store)

        if ctx.check_cancelled():
            return

        from trendradar.domain.signal.repository import SignalRepository
        from trendradar.infrastructure.storage.artifact_store import ArtifactStore
        from trendradar.infrastructure.runtime import runtime_root

        artifact_store = ArtifactStore(runtime_root() / "storage")
        repo = SignalRepository(artifact_store)
        saved_path = repo.save(signal_set, ctx.job_id)

        _write_selection_manifest(ctx.job_id, signal_set)

        summary = {
            "execution_key": signal_set.execution_key,
            "signal_count": len(signal_set.signals),
            "signal_from": str(signal_set.signal_from) if signal_set.signal_from else None,
            "signal_to": str(signal_set.signal_to) if signal_set.signal_to else None,
            "strategies": len(signal_set.strategies_snapshot),
            "saved_path": saved_path,
        }
        ctx.log(f"Saved signals to {saved_path}")
        ctx.succeed(summary)

    return executor.submit("selection", worker, request)


def submit_batch_selection(
    executor: JobExecutor,
    market_store: MarketDataStore,
    request: dict,
    store=None,
) -> str:
    """Submit a batch selection job.

    Args:
        executor: JobExecutor instance.
        market_store: MarketDataStore instance.
        request: dict with optional keys: groups, strategies, start_date, end_date, codes,
                 plus batch-specific: batch_size (int), batch_interval_days (int).
        store: StorageConnection instance.

    Returns:
        job_id: str
    """
    def worker(ctx: JobContext) -> None:
        ctx.log("Starting batch selection job")

        batch_size = request.get("batch_size", 50)
        interval_days = request.get("batch_interval_days", 7)

        signal_set = _run_selection(ctx, market_store, request, store)

        if ctx.check_cancelled():
            return

        if signal_set.signals:
            from trendradar.domain.signal.repository import SignalRepository
            from trendradar.infrastructure.storage.artifact_store import ArtifactStore
            from trendradar.infrastructure.runtime import runtime_root

            artifact_store = ArtifactStore(runtime_root() / "storage")
            repo = SignalRepository(artifact_store)
            saved_path = repo.save(signal_set, ctx.job_id)
            ctx.log(f"Saved signals to {saved_path}")

        _write_selection_manifest(ctx.job_id, signal_set)

        result = {
            "execution_key": signal_set.execution_key,
            "signal_count": len(signal_set.signals),
            "signal_from": str(signal_set.signal_from) if signal_set.signal_from else None,
            "signal_to": str(signal_set.signal_to) if signal_set.signal_to else None,
            "strategies": len(signal_set.strategies_snapshot),
            "mode": "batch",
            "batch_size": batch_size,
            "interval_days": interval_days,
        }
        ctx.succeed(result)

    return executor.submit("batch_selection", worker, request)
