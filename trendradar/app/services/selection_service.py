"""Selection orchestration service."""

from __future__ import annotations

import bisect
import json
import time
from datetime import date, timedelta

import polars as pl

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.executor import JobExecutor
from trendradar.domain.market.data_store import MarketDataStore
from trendradar.domain.signal.models import SignalSet, StrategySignal
from trendradar.domain.strategy.protocol import (
    SelectionContext,
    SelectionStrategy,
    WarmupResult,
)
from trendradar.domain.strategy.registry import get as get_defn, list_all
from trendradar.domain.strategy.resolver import resolve as resolve_strategies
from trendradar.domain.strategy.resolver import validate_request_ids
from trendradar.infrastructure.tushare.market_cap import daily_basic_circ_mv


def _needs_market_cap(resolved) -> bool:
    """Any resolved strategy requires per-day float market cap in context."""
    return any(getattr(d.selector_class, "REQUIRES_MARKET_CAP", False) for d in resolved)


def _build_market_cap_map(pro, trading_dates) -> dict:
    """Per-date {code: circ_mv} map; fetch failures degrade to None (never raise)."""
    result: dict = {}
    for d in trading_dates:
        try:
            result[d] = daily_basic_circ_mv(pro, d)
        except Exception as e:
            result[d] = None
    return result

def _filter_warmup(
    warmup: WarmupResult | None,
    candidate_codes: list,
    trade_date: date,
    dates_by_code: dict[str, list[date]],
) -> WarmupResult | None:
    """Keep only codes that traded on the selection date (suspended stocks have
    stale last bars and must not be judged) and truncate each history at
    trade_date so selectors judge that day, not the range end (no lookahead).

    Histories are date-sorted (runner sorts by [code, date]); bisect + slice
    keeps truncation O(1) per code instead of O(rows) per code per day.
    """
    if warmup is None:
        return None
    grouped = {}
    for c in candidate_codes:
        if c not in warmup.grouped:
            continue
        pos = bisect.bisect_right(dates_by_code[c], trade_date)
        grouped[c] = warmup.grouped[c].slice(0, pos)
    return WarmupResult(grouped=grouped)


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
            "params": json.loads(s["params_json"] or "{}"),
        }

    all_defs = list_all()
    for d in all_defs:
        if d.strategy_id not in settings_map:
            settings_map[d.strategy_id] = {
                "enabled": True,
                "params": dict(d.default_params),
            }

    return group_defs, member_defs, settings_map


VALID_BOARDS = ("主板", "创业板", "科创板", "北交所")


def validate_selection_request(request: dict, store, market_store=None) -> None:
    """提交时同步校验请求（group/strategy id、boards），未知 id/板块抛 ValueError → API 400。

    必须在 enqueue 之前调用：worker 里的 resolve 是静默跳过未知 id 的，等任务跑完
    才发现少了一个策略就太晚了。disabled 不算错误，仍由 resolve 阶段正常跳过。
    boards 校验同样只能在提交前做——worker 内 raise 只会作业 failed，HTTP 早已 202。
    market_store 由调用方传入（三个提交点均已握有）；缺列时 400，绝不静默放宽
    （否则用户点"北交所"会拿到全宇宙，结果看着正常但语义错误）。
    """
    group_defs, _member_defs, _settings_map = _get_strategy_resolve_input(store)
    validate_request_ids(group_defs, request)

    boards = request.get("boards")
    if not boards:
        return
    invalid = sorted({str(b) for b in boards} - set(VALID_BOARDS))
    if invalid:
        raise ValueError(f"未知板块: {invalid}（可选值：{list(VALID_BOARDS)}）")
    if market_store is None:
        raise ValueError("boards 过滤需要 market_store（调用方未传入）")
    meta = market_store.stock_meta()
    if "market" not in meta.columns:
        raise ValueError("stock_meta 缺少 market 列，无法按板块过滤（数据待全量重建后回填）")


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
    need_mcap = _needs_market_cap(resolved)
    mc_map: dict = {}
    if need_mcap:
        from trendradar.infrastructure.tushare.client import get_pro
        ctx.log("策略需要流通市值，按交易日拉取 daily_basic")
        mc_map = _build_market_cap_map(get_pro(), trading_dates)

    max_window = 120
    extended_start = trading_dates[0] - timedelta(days=max_window * 2)
    codes = request.get("codes")
    boards = request.get("boards") or []
    need_meta = not codes or bool(boards)
    meta = market_store.stock_meta() if need_meta else None
    if boards:
        # market 列存在性由提交前校验保证（缺列已在提交前 400），此处无降级分支
        meta = meta.filter(pl.col("market").is_in(boards))
    if codes:
        if boards:
            allowed = set(meta["code"].to_list()) if not meta.is_empty() else set()
            codes = [c for c in codes if c in allowed]
            ctx.log(f"Boards {boards} ∩ whitelist: {len(codes)} stocks")
        else:
            ctx.log(f"Using {len(codes)} specified stocks")
    else:
        codes = meta["code"].to_list() if not meta.is_empty() else []
        if boards:
            ctx.log(f"Boards {boards}: universe {len(codes)} stocks")
        else:
            ctx.log(f"Using all {len(codes)} available stocks")
    if boards and not codes:
        ctx.log(f"板块过滤后宇宙为空（boards={boards}）——正常返回 0 信号")
    codes = sorted(codes)   # 跨输入确定性：白名单原始顺序不改变 picks 顺序

    market_data = market_store.load_bars(codes, extended_start, trading_dates[-1])
    if market_data.is_empty():
        ctx.log("No market data loaded")
        return SignalSet(execution_key=ctx.job_id)
    market_data = market_data.sort(["code", "date"])   # ordering responsibility (runner)
    dates_by_code = {
        g["code"][0]: g["date"].to_list()
        for g in market_data.partition_by("code")
    }

    ctx.log(f"Loaded {market_data.height} market data rows")

    # Phase 1: warmup once per strategy
    warmups: dict[str, WarmupResult | None] = {}
    selectors: dict[str, SelectionStrategy] = {}
    for defn in resolved:
        if ctx.check_cancelled():
            ctx.cancel()
            return SignalSet(execution_key=ctx.job_id)
        ctx.log(f"Warmup {defn.strategy_id}")
        selector = defn.selector_class(defn)
        selectors[defn.strategy_id] = selector
        try:
            warmups[defn.strategy_id] = selector.warmup(market_data)
        except Exception as e:
            ctx.log(f"Error in warmup {defn.strategy_id}: {e}", level="WARN")
            warmups[defn.strategy_id] = None
        ctx.log(f"Warmup done {defn.strategy_id}")

    # Phase 2: per-day select_day
    all_signals: list[StrategySignal] = []
    total_dates = len(trading_dates)
    strategies_snapshot = [
        {"strategy_id": d.strategy_id, "name": d.name,
         # 注册表默认 + DB 覆盖：旧 DB 参数残留不会污染新快照
         "params": {**d.default_params, **settings_map.get(d.strategy_id, {}).get("params", {})}}
        for d in resolved
    ]

    for date_idx, trade_date in enumerate(trading_dates):
        if ctx.check_cancelled():
            ctx.cancel()
            return SignalSet(execution_key=ctx.job_id)
        ctx.update_progress(date_idx + 1, total_dates, str(trade_date))

        day_data = market_data.filter(pl.col("date") == trade_date)
        if day_data.is_empty():
            continue
        day_codes = day_data["code"].unique().to_list()
        candidate_codes = [c for c in codes if c in day_codes]
        if not candidate_codes:
            continue
        context = SelectionContext(
            trade_date=trade_date,
            market_data=market_data,
            candidate_codes=candidate_codes,
            market_cap=mc_map.get(trade_date),
        )

        for defn in resolved:
            if ctx.check_cancelled():
                ctx.cancel()
                return SignalSet(execution_key=ctx.job_id)
            warmup = warmups.get(defn.strategy_id)
            if warmup is None:
                continue
            try:
                selector = selectors[defn.strategy_id]
                # 只判当日有行情的股票：停牌股（最后 bar 早于选股日）不参与判定
                day_warmup = _filter_warmup(warmup, candidate_codes, trade_date, dates_by_code)
                result = selector.select_day(context, day_warmup)
                if result.selected_codes:
                    all_signals.append(StrategySignal(
                        strategy_id=result.strategy_id,
                        strategy_name=result.strategy_name,
                        signal_date=result.trade_date,
                        codes=result.selected_codes,
                    ))
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


def _register_selection_metadata(
    execution_key: str, execution_type: str = "selection"
) -> None:
    """Register executions/artifacts rows once selection artifacts are written."""
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import (
        register_artifacts,
        register_execution,
    )

    storage_root = runtime_root() / "storage"
    selection_dir = (
        storage_root / "objects" / "executions" / execution_key / "selection"
    )
    with StorageConnection(storage_root).connection() as conn:
        register_execution(
            conn,
            execution_key,
            execution_type,
            manifest_key=f"objects/executions/{execution_key}/selection/manifest.json",
            lineage_key=f"objects/executions/{execution_key}/selection/lineage.json",
        )
        register_artifacts(
            conn,
            execution_key,
            "selection",
            list(selection_dir.iterdir()) if selection_dir.exists() else [],
            storage_root,
        )
        conn.commit()


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

        _register_selection_metadata(ctx.job_id)

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
        request: dict with optional keys: groups, strategies, start_date, end_date, codes.
        store: StorageConnection instance.

    Returns:
        job_id: str
    """
    def worker(ctx: JobContext) -> None:
        ctx.log("Starting batch selection job")

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

        _register_selection_metadata(ctx.job_id)

        result = {
            "execution_key": signal_set.execution_key,
            "signal_count": len(signal_set.signals),
            "signal_from": str(signal_set.signal_from) if signal_set.signal_from else None,
            "signal_to": str(signal_set.signal_to) if signal_set.signal_to else None,
            "strategies": len(signal_set.strategies_snapshot),
            "mode": "batch",
        }
        ctx.succeed(result)

    return executor.submit("batch_selection", worker, request)
