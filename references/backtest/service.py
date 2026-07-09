from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping

from backtest import BacktestEngine, default_config
from backtest.config import BacktestConfig, TradeStrategyName
from backtest.reports.writer import create_run_dir, save_run_outputs
from core.runtime import runtime_root


StrategyStartCallback = Callable[[str], None]
StrategyResultCallback = Callable[[str, Mapping[str, object]], None]
ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


def normalize_capital_mode(raw: str) -> str:
    value = str(raw).strip().lower()
    if value == "signal_only":
        return "unlimited_cash"
    return value


def normalize_trade_strategy(raw: str) -> TradeStrategyName:
    value = str(raw or "").strip().lower()
    aliases = {
        "": "long_term_bull_bear_stop",
        "long_term_bull_bear_stop": "long_term_bull_bear_stop",
        "bull_bear_stop": "long_term_bull_bear_stop",
        "zx_stop": "long_term_bull_bear_stop",
        "ten_day_low_stop": "ten_day_low_stop",
        "10d_low_stop": "ten_day_low_stop",
        "recent_low_stop": "ten_day_low_stop",
    }
    normalized = aliases.get(value)
    if normalized is None:
        raise ValueError("交易策略仅支持 不限资金+多空线止损 或 不限资金+10日低点止损")
    return normalized  # type: ignore[return-value]


def trade_strategy_label(value: str) -> str:
    strategy = normalize_trade_strategy(value)
    if strategy == "ten_day_low_stop":
        return "不限资金 + 10日低点止损"
    return "不限资金 + 多空线止损"


def build_config(
    *,
    mode: str = "unlimited_cash",
    cash_per_trade: float = 50_000.0,
    trade_strategy: str = "long_term_bull_bear_stop",
    base_config: BacktestConfig | None = None,
) -> BacktestConfig:
    cfg = base_config or default_config()
    root = runtime_root()
    cfg = replace(
        cfg,
        paths=replace(
            cfg.paths,
            signal_dir=str(root / "storage" / "objects" / "signals"),
            parquet_dir=str(root / "db"),
            storage_root=str(root / "storage"),
            output_root=str(root / "storage" / "objects" / "executions"),
        ),
    )
    mode = normalize_capital_mode(mode)
    if mode not in {"realistic", "unlimited_cash"}:
        raise ValueError("资金模式仅支持 不限资金 或 现金约束")
    if cash_per_trade <= 0:
        raise ValueError("每票金额必须大于0")
    normalized_trade_strategy = normalize_trade_strategy(trade_strategy)
    if normalized_trade_strategy == "ten_day_low_stop":
        execution = replace(
            cfg.execution,
            trade_strategy=normalized_trade_strategy,
            force_sell_on_two_day_close_below_long_term_bull_bear_line=False,
            close_below_recent_low_stop_window=10,
        )
    else:
        execution = replace(
            cfg.execution,
            trade_strategy=normalized_trade_strategy,
            force_sell_on_two_day_close_below_long_term_bull_bear_line=True,
            close_below_recent_low_stop_window=None,
        )
    return replace(
        cfg,
        capital=replace(
            cfg.capital,
            mode=mode,
            fixed_cash_per_trade=float(cash_per_trade),
        ),
        execution=execution,
    )


def run_backtest(
    *,
    start: date,
    end: date,
    strategies: list[str] | None = None,
    mode: str = "unlimited_cash",
    cash_per_trade: float = 50_000.0,
    trade_strategy: str = "long_term_bull_bear_stop",
    run_name: str | None = None,
    signal_files: list[Path] | None = None,
    signal_source: str | None = None,
    selection_execution_keys: list[str] | None = None,
    on_strategy_start: StrategyStartCallback | None = None,
    on_strategy_result: StrategyResultCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
) -> dict[str, object]:
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")

    if on_progress:
        on_progress(0, 0, "正在初始化回测引擎")
    if on_log:
        on_log("初始化回测引擎")
    cfg = build_config(mode=mode, cash_per_trade=cash_per_trade, trade_strategy=trade_strategy)
    engine = BacktestEngine(cfg, signal_files=signal_files)
    strategy_list = strategies or engine.list_available_strategies(start, end)
    strategy_list = [s for s in strategy_list if str(s).strip()]
    if not strategy_list:
        raise ValueError("未找到可回测策略")

    total = len(strategy_list)
    if on_progress:
        on_progress(0, total, f"准备回测 {total} 个策略")
    if on_log:
        on_log(f"回测开始: {start.isoformat()} ~ {end.isoformat()}，策略 {total} 个")

    strategy_results: dict[str, Mapping[str, object]] = {}
    for index, strategy_name in enumerate(strategy_list, 1):
        if on_progress:
            on_progress(index - 1, total, f"正在回测 {strategy_name}")
        if on_log:
            on_log(f"{strategy_name} 开始")
        if on_strategy_start:
            on_strategy_start(strategy_name)
        result = engine.run(start=start, end=end, strategy_name=strategy_name)
        strategy_results[strategy_name] = result
        if on_strategy_result:
            on_strategy_result(strategy_name, result)
        if on_log:
            on_log(f"{strategy_name} 完成")
        if on_progress:
            on_progress(index, total, f"{strategy_name} 完成")

    run_base_dir = create_run_dir(
        output_root=Path(cfg.paths.output_root),
        start=start,
        end=end,
        run_name=run_name,
    )
    execution_key = run_base_dir.name
    run_dir = run_base_dir / "backtest"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "execution_key": execution_key,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "strategies": strategy_list,
        "strategy_snapshots": _load_strategy_snapshots(strategy_list),
        "signal_dir": signal_source or cfg.paths.signal_dir,
        "signal_files": [str(path) for path in signal_files] if signal_files else [],
        "selection_execution_keys": selection_execution_keys or [],
        "selection_from": start.isoformat() if selection_execution_keys else None,
        "selection_to": end.isoformat() if selection_execution_keys else None,
        "capital_mode": cfg.capital.mode,
        "cash_per_trade": float(cfg.capital.fixed_cash_per_trade),
        "trade_rule": {
            "trade_strategy": cfg.execution.trade_strategy,
            "trade_strategy_name": trade_strategy_label(cfg.execution.trade_strategy),
            "signal": "T",
            "buy": "T+1 open",
            "sell": f"T+{cfg.execution.fixed_hold_n_days + 1} close",
            "hold_n_days": int(cfg.execution.fixed_hold_n_days),
            "连续两日收盘低于长期多空线强制卖出": bool(
                cfg.execution.force_sell_on_two_day_close_below_long_term_bull_bear_line
            ),
            "close_below_recent_low_stop_window": cfg.execution.close_below_recent_low_stop_window,
        },
    }
    save_run_outputs(
        run_dir=run_dir,
        meta=meta,
        strategy_results=strategy_results,
        storage_root=Path(cfg.paths.storage_root),
    )
    return {
        "execution_key": execution_key,
        "run_dir": run_dir,
        "strategies": strategy_list,
        "strategy_results": strategy_results,
    }


def _load_strategy_snapshots(strategy_list: list[str]) -> list[dict[str, object]]:
    config_path = runtime_root() / "configs.json"
    if not config_path.exists():
        return [{"name": name, "class": "", "description": "", "params": {}} for name in strategy_list]

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    by_alias: dict[str, dict[str, object]] = {}
    for item in selectors:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias", "")).strip()
        if not alias:
            continue
        by_alias[alias] = {
            "name": alias,
            "class": str(item.get("class", "")).strip(),
            "description": str(item.get("_comment", "")).strip(),
            "params": item.get("params", {}),
        }

    return [
        by_alias.get(name, {"name": name, "class": "", "description": "", "params": {}})
        for name in strategy_list
    ]
