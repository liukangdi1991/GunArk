"""Backtest orchestration service."""

from __future__ import annotations

from datetime import date

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.executor import JobExecutor
from trendradar.domain.backtest.engine import BacktestEngine, BacktestResult
from trendradar.domain.backtest.config import (
    BacktestConfig,
    CapitalConfig,
    CostConfig,
    ExecutionConfig,
    PortfolioConfig,
    RiskConfig,
)
from trendradar.domain.market.data_store import MarketDataStore
from trendradar.domain.signal.models import SignalSet
from trendradar.domain.signal.repository import SignalRepository


def validate_backtest_prerequisites(
    signal_set: SignalSet,
    trading_dates: list[date],
    fixed_hold_n_days: int = 5,
    entry_on_signal_day: bool = False,
) -> list[str]:
    """Return blocking reasons for a backtest; empty list means it can run.

    Rule (V1): signal at T, buy at T+1 open, target sell at T+N+1 close, all
    in trading-day indices. A signal whose date leaves fewer than N+1 trading
    days afterwards is skipped by the engine. If EVERY signal is skipped the
    backtest produces zero trades — block it up front. If at least one signal
    date is tradeable, the backtest runs (only the tail signals are skipped).
    """
    if not signal_set.signals:
        return ["信号集为空，没有可回测的信号。"]

    signal_dates = sorted({s.signal_date for s in signal_set.signals if s.signal_date})
    if not signal_dates:
        return ["信号集缺少信号日期，无法回测。"]

    cal_index = {d: i for i, d in enumerate(trading_dates)}
    # 信号日入场（entry_on_signal_day）当天即可买入；否则 T+1 买入
    needed = fixed_hold_n_days + (0 if entry_on_signal_day else 1)
    tradeable = [
        d
        for d in signal_dates
        if cal_index.get(d) is not None and cal_index[d] + needed < len(trading_dates)
    ]
    if tradeable:
        return []

    last_sig = signal_dates[-1]
    last_idx = cal_index.get(last_sig)
    if last_idx is None:
        return [f"信号日 {last_sig} 不在交易日历中，无法确定买入日。"]

    entry_desc = "信号日买入" if entry_on_signal_day else "T+1 买入"
    return [
        f"信号集内所有信号日均无足够交易日完成买入+持仓（需 {needed} 个："
        f"{entry_desc} + 持仓 {fixed_hold_n_days} 日）。最新信号日 {last_sig} 之后仅剩 "
        f"{len(trading_dates) - 1 - last_idx} 个交易日，回测将没有任何交易。"
        f"请选择更早的选股区间，或先同步更新行情数据（当前行情截止 {trading_dates[-1]}）。"
    ]


def _build_config(request: dict) -> BacktestConfig:
    cap = request.get("capital", {})
    exe = request.get("execution", {})
    cost = request.get("costs", {})
    port = request.get("portfolio", {})
    risk = request.get("risk", {})

    capital = CapitalConfig(
        initial_cash=cap.get("initial_cash", 1_000_000.0),
        lot_size=cap.get("lot_size", 100),
        position_budget_cash=cap.get("position_budget_cash", 100_000.0),
        max_cash_usage_pct=cap.get("max_cash_usage_pct"),
        mode=cap.get("mode", "unlimited_cash"),
        fixed_cash_per_trade=cap.get("fixed_cash_per_trade", 50_000.0),
    )
    execution = ExecutionConfig(
        fixed_hold_n_days=exe.get("fixed_hold_n_days", 5),
        max_sell_postpone_days=exe.get("max_sell_postpone_days", 10),
        reject_if_limit_up_on_buy=exe.get("reject_if_limit_up_on_buy", True),
        postpone_if_limit_down_on_sell=exe.get("postpone_if_limit_down_on_sell", True),
        force_sell_on_two_day_close_below_long_term_bull_bear_line=exe.get(
            "force_sell_on_two_day_close_below_long_term_bull_bear_line", False
        ),
        close_below_recent_low_stop_window=exe.get("close_below_recent_low_stop_window"),
        entry_on_signal_day=exe.get("entry_on_signal_day", False),
        entry_at_close=exe.get("entry_at_close", False),
    )
    costs = CostConfig(
        commission_rate=cost.get("commission_rate", 0.0003),
        commission_min=cost.get("commission_min", 5.0),
        stamp_duty_rate_sell=cost.get("stamp_duty_rate_sell", 0.0001),
        transfer_fee_rate=cost.get("transfer_fee_rate", 0.00001),
        slippage_buy_bp=cost.get("slippage_buy_bp", 2.0),
        slippage_sell_bp=cost.get("slippage_sell_bp", 2.0),
    )
    portfolio = PortfolioConfig(
        target_positions=port.get("target_positions"),
        max_positions=port.get("max_positions"),
        max_single_position_pct=port.get("max_single_position_pct"),
        max_daily_new_positions=port.get("max_daily_new_positions"),
        allow_reentry_same_stock=port.get("allow_reentry_same_stock", False),
    )
    risk = RiskConfig(
        benchmark=risk.get("benchmark", "000300.SH"),
        trading_days_per_year=risk.get("trading_days_per_year", 252),
        risk_free_rate=risk.get("risk_free_rate", 0.02),
    )

    return BacktestConfig(
        capital=capital,
        execution=execution,
        costs=costs,
        portfolio=portfolio,
        risk=risk,
    )


def _run_backtest_worker(
    ctx: JobContext,
    signal_set: SignalSet,
    market_store: MarketDataStore,
    config: BacktestConfig,
    result_json_path: str | None = None,
) -> None:
    ctx.log(f"Running backtest with {len(signal_set.signals)} signals")
    ctx.log(f"Backtest config: capital_mode={config.capital.mode}, "
            f"initial_cash={config.capital.initial_cash}")

    engine = BacktestEngine(config)

    def progress(current: int, total: int) -> None:
        if ctx.check_cancelled():
            return
        ctx.update_progress(current, total)

    def cancel_check() -> bool:
        return ctx.check_cancelled()

    result = engine.run(
        signal_set=signal_set,
        market_store=market_store,
        progress=progress,
        cancel_check=cancel_check,
    )

    if ctx.check_cancelled():
        ctx.fail("Cancelled by user")
        return

    trades_data = [
        {
            "execution_key": ctx.job_id,
            "strategy": t.strategy,
            "code": t.code,
            "signal_date": str(t.signal_date),
            "buy_date": str(t.buy_date),
            "sell_date": str(t.sell_date),
            "buy_price": t.buy_price,
            "sell_price": t.sell_price,
            "shares": t.shares,
            "profit": t.profit,
            "return_pct": t.return_pct,
            "sell_postpone_days": t.sell_postpone_days,
        }
        for t in result.trades
    ]

    skip_data = [
        {
            "execution_key": ctx.job_id,
            "strategy": s.strategy,
            "code": s.code,
            "signal_date": str(s.signal_date),
            "buy_date": str(s.buy_date) if s.buy_date else None,
            "stage": s.stage,
            "reason": s.reason,
        }
        for s in result.skips
    ]

    equity_data = [
        {
            "execution_key": ctx.job_id,
            "strategy": "",
            "date": str(e["date"]),
            "cash": e["cash"],
            "equity": e["equity"],
            "position_count": e["position_count"],
        }
        for e in result.equity_curve
    ]

    metrics = dict(result.metrics)
    metrics["initial_cash"] = config.capital.initial_cash
    metrics["final_cash"] = (
        result.equity_curve[-1]["cash"] if result.equity_curve else config.capital.initial_cash
    )

    output = {
        "metrics": metrics,
        "trade_count": len(result.trades),
        "skip_count": len(result.skips),
        "trades": trades_data,
        "skips": skip_data,
        "equity_curve": equity_data,
    }

    ctx.log(f"Backtest complete: {result.metrics.get('trade_count', 0)} trades, "
            f"total_return={result.metrics.get('total_return_pct', 0):.2f}%, "
            f"win_rate={result.metrics.get('win_rate_pct', 0):.2f}%, "
            f"sharpe={result.metrics.get('sharpe', 0):.2f}")

    if result_json_path:
        import json
        from pathlib import Path
        import polars as pl

        out_dir = Path(result_json_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)

        if trades_data:
            pl.DataFrame(trades_data).write_parquet(out_dir / "trades.parquet")
        if skip_data:
            pl.DataFrame(skip_data).write_parquet(out_dir / "skips.parquet")
        if equity_data:
            pl.DataFrame(equity_data).write_parquet(out_dir / "equity.parquet")

        Path(out_dir / "metrics.json").write_text(
            json.dumps(result.metrics, ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
        Path(out_dir / "result.json").write_text(
            json.dumps(output, ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )

        ctx.log(f"Saved results to {out_dir}")

        _register_backtest_metadata(ctx.job_id, out_dir)

        # 组合管线（selection_backtest）中选股与回测共用同一 job_id——
        # 跳过自引用链接，避免血缘把回测自身当成来源选股
        if signal_set.execution_key != ctx.job_id:
            _link_backtest_to_selection(signal_set.execution_key, ctx.job_id)

    ctx.succeed(output)


def _link_backtest_to_selection(selection_key: str, backtest_key: str) -> None:
    """Insert execution_links row connecting selection to backtest."""
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection

    with StorageConnection(runtime_root() / "storage").connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_links "
            "(source_execution_key, target_execution_key, link_type) "
            "VALUES (?, ?, 'backtest_uses_selection')",
            (selection_key, backtest_key),
        )
        conn.commit()


def _register_backtest_metadata(execution_key: str, out_dir) -> None:
    """Register executions/artifacts rows once backtest artifacts are written."""
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import (
        register_artifacts,
        register_execution,
    )

    storage_root = runtime_root() / "storage"
    with StorageConnection(storage_root).connection() as conn:
        register_execution(conn, execution_key, "backtest")
        register_artifacts(
            conn,
            execution_key,
            "backtest",
            list(out_dir.iterdir()) if out_dir.exists() else [],
            storage_root,
        )
        conn.commit()


def submit_backtest(
    executor: JobExecutor,
    market_store: MarketDataStore,
    signal_repo: SignalRepository,
    request: dict,
) -> str:
    """Submit a backtest job using an existing signal set.

    Args:
        executor: JobExecutor instance.
        market_store: MarketDataStore instance for price data.
        signal_repo: SignalRepository instance for loading signals.
        request: dict with keys:
            - execution_key: str (required) — which signal set to backtest
            - capital: dict (optional)
            - execution: dict (optional)
            - costs: dict (optional)
            - portfolio: dict (optional)
            - risk: dict (optional)

    Returns:
        job_id: str
    """
    def worker(ctx: JobContext) -> None:
        ctx.log("Starting backtest job")

        execution_key = request.get("execution_key")
        if not execution_key:
            ctx.fail("Missing required field: execution_key")
            return

        ctx.log(f"Loading signal set: {execution_key}")
        signal_set = signal_repo.load(execution_key)
        if signal_set is None:
            ctx.fail(f"Signal set not found: {execution_key}")
            return

        config = _build_config(request)
        from trendradar.infrastructure.runtime import runtime_root
        result_path = str(
            runtime_root() / "storage" / "objects" / "executions" / ctx.job_id / "backtest" / "result.json"
        )
        _run_backtest_worker(ctx, signal_set, market_store, config, result_path)

    return executor.submit("backtest", worker, request)


def submit_selection_backtest(
    executor: JobExecutor,
    market_store: MarketDataStore,
    signal_repo: SignalRepository,
    request: dict,
) -> str:
    """Submit a combined selection + backtest job.

    This runs selection first using the same request parameters, then
    immediately runs the backtest on the resulting signals.

    Args:
        executor: JobExecutor instance.
        market_store: MarketDataStore instance.
        signal_repo: SignalRepository instance.
        request: dict with selection params (groups, strategies, codes, start_date, end_date)
                 and optional backtest config under 'backtest' key.

    Returns:
        job_id: str
    """
    def worker(ctx: JobContext) -> None:
        ctx.log("Starting selection + backtest pipeline")

        from trendradar.app.services.selection_service import (
            _register_selection_metadata,
            _run_selection,
            _write_selection_manifest,
        )
        from trendradar.infrastructure.runtime import runtime_root
        from trendradar.infrastructure.storage.artifact_store import ArtifactStore
        from trendradar.infrastructure.storage.connection import StorageConnection

        storage_root = runtime_root() / "storage"
        store = StorageConnection(storage_root)

        ctx.log("Phase 1: Running selection")
        signal_set = _run_selection(ctx, market_store, request, store)

        if ctx.check_cancelled():
            return

        ctx.log(f"Selection produced {len(signal_set.signals)} signals")

        artifact_store = ArtifactStore(storage_root)
        repo = SignalRepository(artifact_store)
        saved_path = repo.save(signal_set, ctx.job_id)
        ctx.log(f"Saved signals to {saved_path}")

        _write_selection_manifest(ctx.job_id, signal_set)
        _register_selection_metadata(ctx.job_id, execution_type="selection_backtest")

        ctx.log("Phase 2: Running backtest")
        backtest_request = request.get("backtest", {})
        config = _build_config(backtest_request)

        result_json_path = str(
            storage_root / "objects" / "executions" / ctx.job_id / "backtest" / "result.json"
        )
        ctx.update_progress(0, 1, "Running backtest")

        _run_backtest_worker(ctx, signal_set, market_store, config, result_json_path)

    return executor.submit("selection_backtest", worker, request)
