from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import polars as pl

from backtest.service import run_backtest as run_backtest_job
from web.core.config import ROOT, storage
from web.schemas.backtest import BacktestFromSelectionRequest, BacktestRequest, SelectionBacktestRequest
from web.schemas.selection import BatchSelectionRequest
from web.services import selection_service

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


def create_backtest(
    payload: BacktestRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    result = run_backtest_job(
        start=_parse_date(payload.from_date),
        end=_parse_date(payload.to_date),
        strategies=payload.strategies,
        mode=payload.mode,
        cash_per_trade=payload.cash_per_trade,
        trade_strategy=payload.trade_strategy,
        run_name=payload.run_name,
        on_progress=progress,
        on_log=log,
    )
    execution_key = str(result["execution_key"])
    return {
        "execution_key": execution_key,
        "run_dir": str(result["run_dir"]),
        "strategies": result["strategies"],
    }


def create_backtest_from_selection(
    payload: BacktestFromSelectionRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    signal_files, start, end, source_keys = _resolve_selection_signal_files(payload.selection_execution_keys)
    if log:
        log(
            f"使用选股历史回测: {len(source_keys)} 个选股结果，"
            f"信号日期 {start.isoformat()} ~ {end.isoformat()}"
        )
    result = run_backtest_job(
        start=start,
        end=end,
        strategies=payload.strategies,
        mode=payload.mode,
        cash_per_trade=payload.cash_per_trade,
        trade_strategy=payload.trade_strategy,
        run_name=payload.run_name,
        signal_files=signal_files,
        signal_source="selection_history",
        selection_execution_keys=source_keys,
        on_progress=progress,
        on_log=log,
    )
    execution_key = str(result["execution_key"])
    return {
        "execution_key": execution_key,
        "run_dir": str(result["run_dir"]),
        "strategies": result["strategies"],
        "selection_execution_keys": source_keys,
    }


def create_selection_backtest(
    payload: SelectionBacktestRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    start = _parse_date(payload.from_date)
    end = _parse_date(payload.to_date)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if progress:
        progress(0, 2, "正在执行选股")
    if log:
        log(f"选股回测开始: 先选股 {start.isoformat()} ~ {end.isoformat()}")

    selection_result = selection_service.create_batch_selection(
        BatchSelectionRequest(
            **{
                "from": start.isoformat(),
                "to": end.isoformat(),
                "strategies": payload.strategies,
            }
        ),
        progress=None,
        log=log,
    )
    if selection_result.get("execution_key"):
        selection_keys = [str(selection_result["execution_key"])]
    else:
        selection_keys = [
            str(item.get("execution_key"))
            for item in selection_result.get("results", [])
            if item.get("execution_key")
        ]
    if not selection_keys:
        raise ValueError("选股回测未生成可回测的选股结果")

    if progress:
        progress(1, 2, "正在执行回测")
    backtest = create_backtest_from_selection(
        BacktestFromSelectionRequest(
            selection_execution_keys=selection_keys,
            strategies=payload.strategies,
            mode=payload.mode,
            cash_per_trade=payload.cash_per_trade,
            trade_strategy=payload.trade_strategy,
            run_name=payload.run_name,
        ),
        progress=None,
        log=log,
    )
    if progress:
        progress(2, 2, "选股回测完成")
    backtest["selection_execution_keys"] = selection_keys
    return backtest


def list_backtests(limit: int = 50) -> dict[str, Any]:
    return {"results": storage.list_backtest_results(limit=limit)}


def get_backtest(execution_key: str) -> dict[str, Any] | None:
    result = storage.get_backtest_result(execution_key)
    if result is None:
        return None
    return {"result": result, "artifacts": storage.list_artifacts(execution_key)}


def get_backtest_report(execution_key: str) -> dict[str, Any] | None:
    result = storage.get_backtest_result(execution_key)
    if result is None:
        return None
    trades = _read_artifact_records(execution_key, "trades_parquet")
    skips = _read_artifact_records(execution_key, "skips_parquet")
    equity = _read_artifact_records(execution_key, "equity_parquet")
    stock_meta = _load_stock_meta()
    _attach_stock_meta(trades, stock_meta)
    _attach_stock_meta(skips, stock_meta)
    _normalize_legacy_unlimited_cash_report(result, trades, equity)
    return {
        "result": result,
        "artifacts": storage.list_artifacts(execution_key),
        "trades": trades,
        "equity": equity,
        "skips": skips,
    }


def delete_backtest(execution_key: str) -> dict[str, Any]:
    return storage.delete_backtest_results([execution_key])


def delete_backtests(execution_keys: list[str] | None = None) -> dict[str, Any]:
    return storage.delete_backtest_results(execution_keys)


def _parse_date(value: str) -> date:
    value = str(value).strip()
    if "-" in value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.strptime(value, "%Y%m%d").date()


def _resolve_selection_signal_files(execution_keys: list[str]) -> tuple[list[Path], date, date, list[str]]:
    source_keys = _normalize_execution_keys(execution_keys)
    if not source_keys:
        raise ValueError("请选择至少一个选股历史")

    signal_files: list[Path] = []
    signal_dates: list[date] = []
    missing: list[str] = []
    for execution_key in source_keys:
        selection = storage.get_selection_result(execution_key)
        if selection is None:
            missing.append(execution_key)
            continue
        paths = _selection_signal_file_paths(execution_key, selection)
        if not paths:
            raise ValueError(f"选股结果缺少 signals.json: {execution_key}")
        signal_files.extend(paths)
        signal_dates.extend(signal_file_date for signal_file_date in (_signal_file_date(path) for path in paths) if signal_file_date)

    if missing:
        raise ValueError(f"选股历史不存在: {', '.join(missing)}")
    if not signal_files or not signal_dates:
        raise ValueError("选股历史没有可用信号文件")
    return sorted(signal_files, key=_signal_file_date_sort_key), min(signal_dates), max(signal_dates), source_keys


def _selection_signal_file_paths(execution_key: str, selection: dict[str, Any]) -> list[Path]:
    raw_signal_file = str(selection.get("signal_file") or "").strip()
    if raw_signal_file:
        path = Path(raw_signal_file)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return _expand_signal_path(path)

    artifacts = storage.list_artifacts(execution_key, run_type="selection")
    match = next((item for item in artifacts if item["artifact_type"] == "signals_json"), None)
    if match is None:
        return []
    return _expand_signal_path(storage.artifact_path(match["storage_key"]))


def _expand_signal_path(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(path.glob("*.json"), key=_signal_file_date_sort_key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [path]
    if not isinstance(payload, dict) or "signal_files" not in payload:
        return [path]
    files: list[Path] = []
    for raw in payload.get("signal_files") or []:
        item = Path(str(raw))
        if not item.is_absolute():
            item = path.parent / item
        if item.exists():
            files.append(item)
    return sorted(files, key=_signal_file_date_sort_key)


def _signal_file_date(path: Path) -> date | None:
    from backtest.data.signal_data import signal_file_date

    try:
        return signal_file_date(path)
    except Exception:
        return None


def _signal_file_date_sort_key(path: Path) -> date:
    return _signal_file_date(path) or date.max


def _normalize_execution_keys(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen = set()
    for value in values:
        key = str(value or "").strip()
        if key and key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized


def _read_artifact_records(execution_key: str, artifact_type: str) -> list[dict[str, Any]]:
    artifacts = storage.list_artifacts(execution_key)
    match = next((item for item in artifacts if item["artifact_type"] == artifact_type), None)
    if match is None:
        return []
    path = storage.artifact_path(match["storage_key"])
    if not path.exists():
        return []
    return [_normalize_execution_key_field(_jsonable_record(row)) for row in pl.read_parquet(path).to_dicts()]


def _jsonable_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in row.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _normalize_execution_key_field(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    return out


@lru_cache(maxsize=1)
def _load_stock_meta() -> dict[str, dict[str, str]]:
    stocklist = ROOT / "stocklist.csv"
    if not stocklist.exists():
        return {}
    try:
        with stocklist.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            return {
                _normalize_code(row.get("symbol")): {
                    "name": str(row.get("name") or "").strip(),
                    "industry": str(row.get("industry") or "").strip(),
                }
                for row in reader
                if _normalize_code(row.get("symbol"))
            }
    except Exception:
        return {}


def _attach_stock_meta(records: list[dict[str, Any]], stock_meta: dict[str, dict[str, str]]) -> None:
    for record in records:
        code = _normalize_code(record.get("code"))
        meta = stock_meta.get(code, {})
        record["code"] = code
        record["name"] = str(record.get("name") or meta.get("name") or "")
        record["industry"] = str(record.get("industry") or meta.get("industry") or "")


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.zfill(6)


def _normalize_legacy_unlimited_cash_report(
    run: dict[str, Any],
    trades: list[dict[str, Any]],
    equity: list[dict[str, Any]],
) -> None:
    if run.get("capital_mode") != "unlimited_cash":
        return
    summaries = run.get("summary") or []
    if any("capital_base" in item for item in summaries if isinstance(item, dict)):
        return

    capital_by_strategy: dict[str, float] = {}
    for trade in trades:
        strategy = str(trade.get("strategy") or "")
        total_cost = float(trade.get("total_cost") or 0.0)
        if strategy and total_cost > 0:
            capital_by_strategy[strategy] = capital_by_strategy.get(strategy, 0.0) + total_cost

    equity_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in equity:
        strategy = str(row.get("strategy") or "")
        if strategy:
            equity_by_strategy.setdefault(strategy, []).append(row)

    for strategy, rows in equity_by_strategy.items():
        rows.sort(key=lambda item: str(item.get("date") or ""))
        capital_base = capital_by_strategy.get(strategy, 0.0)
        if capital_base <= 0 or not rows:
            continue
        raw_initial = float(rows[0].get("equity") or 0.0)
        if raw_initial <= 0:
            continue
        for row in rows:
            raw_equity = float(row.get("equity") or 0.0)
            row["raw_equity"] = raw_equity
            row["capital_base"] = capital_base
            row["equity"] = capital_base + (raw_equity - raw_initial)

    summary_by_strategy = {
        str(item.get("strategy") or ""): item
        for item in summaries
        if isinstance(item, dict)
    }
    for strategy, rows in equity_by_strategy.items():
        summary = summary_by_strategy.get(strategy)
        if summary is None:
            continue
        values = [float(row.get("equity") or 0.0) for row in rows]
        capital_base = capital_by_strategy.get(strategy, 0.0)
        if not values or capital_base <= 0:
            continue
        summary["capital_base"] = capital_base
        summary["final_equity"] = values[-1]
        _update_summary_from_equity_values(summary, values)


def _update_summary_from_equity_values(summary: dict[str, Any], values: list[float]) -> None:
    initial = values[0]
    final = values[-1]
    if initial <= 0:
        return
    total_return = final / initial - 1.0
    periods = max(1, len(values) - 1)
    summary["total_return_pct"] = total_return * 100.0
    summary["annual_return_pct"] = ((1 + total_return) ** (252 / periods) - 1.0) * 100.0 if total_return > -1 else -100.0

    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    summary["max_drawdown_pct"] = max_drawdown * 100.0

    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    if len(returns) < 2 or sum(abs(item) for item in returns) <= 1e-12:
        summary["sharpe"] = 0.0
        return
    excess = [item - 0.02 / 252 for item in returns]
    vol = statistics.stdev(excess)
    summary["sharpe"] = 0.0 if vol <= 1e-12 else math.sqrt(252) * statistics.mean(excess) / vol
