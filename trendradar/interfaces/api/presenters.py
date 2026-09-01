"""Presentation helpers: aggregate V2 runtime data into the frontend contract.

The Web workbench (frontend/src/types/*.ts) expects bare, rich response
objects — no {"data": ...} wrapper. These functions build those shapes from
the artifact files, SQLite metadata, and the strategy registry.
"""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path

from trendradar.infrastructure.runtime import runtime_root

_MIME = {
    ".json": "application/json",
    ".parquet": "application/octet-stream",
    ".log": "text/plain",
}

_PROGRESS_RE = re.compile(r"\[PROGRESS\] (\d+)/(\d+) \((\d+)%\)(.*)")


def _storage_root() -> Path:
    return runtime_root() / "storage"


def _executions_root() -> Path:
    return _storage_root() / "objects" / "executions"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------


def strategy_list_payload() -> dict:
    from trendradar.domain.strategy.registry import list_all

    return {
        "strategies": [
            {
                "name": d.name,
                "class": d.selector_class.__name__,
                "description": d.description,
                "params": dict(d.default_params),
            }
            for d in list_all()
        ]
    }


def _strategies_names_to_ids(names: list[str] | None) -> list[str] | None:
    if not names:
        return None
    from trendradar.domain.strategy.registry import list_all

    by_name = {d.name: d.strategy_id for d in list_all()}
    return [by_name.get(n, n) for n in names if by_name.get(n, n)]


# ---------------------------------------------------------------------------
# selection results
# ---------------------------------------------------------------------------


def _selection_artifacts(key: str) -> list[dict]:
    sel_dir = _executions_root() / key / "selection"
    artifacts = []
    if sel_dir.is_dir():
        for f in sorted(sel_dir.iterdir()):
            if f.is_file():
                artifacts.append(_artifact_row(key, f, scope="execution"))
    return artifacts


def _artifact_row(key: str, path: Path, scope: str) -> dict:
    rel = path.relative_to(_storage_root()).as_posix()
    return {
        "artifact_scope": scope,
        "artifact_type": path.name,
        "storage_key": rel,
        "mime_type": _MIME.get(path.suffix.lower(), "application/octet-stream"),
        "size_bytes": path.stat().st_size,
        "checksum": "",
        "created_at": _file_mtime_iso(path),
    }


def _selection_summary(signals: dict) -> list[dict]:
    by_strategy: dict[str, dict] = {}
    for sig in signals.get("signals", []):
        name = sig.get("strategy_name") or sig.get("strategy_id") or "未知"
        date_str = sig.get("signal_date") or ""
        count = len(sig.get("codes") or [])
        key = (name, date_str)
        if key not in by_strategy:
            by_strategy[key] = {
                "strategy": name,
                "date": date_str,
                "count": 0,
                "elapsed_seconds": 0.0,
            }
        by_strategy[key]["count"] += count
    return list(by_strategy.values())


def _selection_picks(key: str, signals: dict) -> list[dict]:
    meta = _stock_meta_by_code()
    picks = []
    for sig in signals.get("signals", []):
        name = sig.get("strategy_name") or sig.get("strategy_id") or "未知"
        date_str = sig.get("signal_date") or ""
        for code in sig.get("codes") or []:
            stock_name, industry = meta.get(str(code), ("", ""))
            picks.append(
                {
                    "execution_key": key,
                    "strategy": name,
                    "date": date_str,
                    "code": str(code),
                    "name": stock_name,
                    "industry": industry,
                }
            )
    return picks


def _selection_snapshots(lineage: dict) -> list[dict]:
    from trendradar.domain.strategy.registry import get

    snapshots = []
    for item in (lineage or {}).get("resolved_strategies", []):
        defn = get(item.get("strategy_id", ""))
        snapshots.append(
            {
                "name": item.get("name") or (defn.name if defn else ""),
                "class": defn.selector_class.__name__ if defn else "",
                "description": defn.description if defn else "",
                "params": item.get("params") or (dict(defn.default_params) if defn else {}),
            }
        )
    return snapshots


def selection_result_payload(key: str, include_detail: bool = False) -> dict:
    sel_dir = _executions_root() / key / "selection"
    manifest = _read_json(sel_dir / "manifest.json", {})
    lineage = _read_json(sel_dir / "lineage.json", {})
    signals = _read_json(sel_dir / "signals.json", {})

    strategies = [s.get("name") for s in lineage.get("resolved_strategies", [])]
    signal_from = signals.get("signal_from")
    signal_to = signals.get("signal_to")

    result = {
        "execution_key": key,
        "created_at": manifest.get("created_at"),
        "finished_at": None,
        "selection_date": signal_to or signal_from or "",
        "strategies": strategies,
        "strategy_snapshots": _selection_snapshots(lineage),
        "status": manifest.get("status", "success"),
        "summary": _selection_summary(signals),
        "selection_from": signal_from,
        "selection_to": signal_to,
        "trade_days": 1 if (signal_from and signal_from == signal_to) else None,
    }

    if not include_detail:
        return result

    return {
        "result": result,
        "artifacts": _selection_artifacts(key),
        "picks": _selection_picks(key, signals),
    }


def list_selection_results_payload(limit: int | None = None) -> dict:
    root = _executions_root()
    results = []
    if root.is_dir():
        for exec_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if limit is not None and len(results) >= limit:
                break
            if not (exec_dir / "selection" / "signals.json").exists():
                continue
            try:
                results.append(selection_result_payload(exec_dir.name))
            except Exception:
                continue
    return {"results": results}


# ---------------------------------------------------------------------------
# backtest results
# ---------------------------------------------------------------------------


def _stock_meta_map() -> dict:
    """code -> {name, industry} from stock_meta.parquet (前端"名称/板块"列）。"""
    import polars as pl

    from trendradar.infrastructure.runtime import runtime_root

    path = runtime_root() / "storage" / "market" / "stock_meta.parquet"
    try:
        df = pl.read_parquet(path)
        return {
            r["code"]: {"name": r.get("name"), "industry": r.get("industry")}
            for r in df.to_dicts()
        }
    except Exception:
        return {}


def _enrich_stock_info(rows: list, meta_map: dict) -> list:
    """Trades/skips 补 name/industry（缺失代码保持原样）。"""
    for r in rows:
        info = meta_map.get(r.get("code"))
        if info:
            r["name"] = info.get("name")
            r["industry"] = info.get("industry")
    return rows


def _backtest_config(key: str) -> dict:
    """Read the original request (mode/cash) from the jobs table."""
    import sqlite3

    db = _storage_root() / "app.db"
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT request_json FROM jobs WHERE job_id = ?", (key,)
        ).fetchone()
        conn.close()
    except Exception:
        row = None
    if not row or not row[0]:
        return {"capital_mode": "unlimited_cash", "cash_per_trade": 50000}
    request = json.loads(row[0])
    params = request.get("params") or {}
    # execution/capital 可能位于顶层（backtest_from_selection）或 backtest 下（selection_backtest）
    execution = request.get("execution") or (request.get("backtest") or {}).get("execution") or {}
    capital = request.get("capital") or (request.get("backtest") or {}).get("capital") or {}
    return {
        "capital_mode": params.get("mode") or capital.get("mode", "unlimited_cash"),
        "cash_per_trade": params.get("cash_per_trade") or capital.get("fixed_cash_per_trade", 50000),
        "execution": execution,
        "trade_strategy": params.get("trade_strategy") or request.get("trade_strategy"),
    }


def _backtest_summary(key: str, result: dict, metrics: dict) -> list[dict]:
    trades = result.get("trades", [])
    skips = result.get("skips", [])
    by_strategy: dict[str, dict] = {}
    for t in trades:
        strat = t.get("strategy") or "未知"
        row = by_strategy.setdefault(
            strat,
            {
                "strategy": strat,
                "trade_count": 0,
                "skip_count": 0,
                "win_rate_pct": 0.0,
                "total_return_pct": 0.0,
                "annual_return_pct": metrics.get("annual_return_pct") or 0.0,
                "max_drawdown_pct": metrics.get("max_drawdown_pct") or 0.0,
                "sharpe": metrics.get("sharpe") or 0.0,
                "final_cash": metrics.get("final_cash") or 0.0,
                "initial_cash": metrics.get("initial_cash") or 0.0,
                "open_positions": 0,
                "capital_mode": "",
                "fixed_cash_per_trade": 0.0,
                "start_date": "",
                "end_date": "",
            },
        )
        row["trade_count"] += 1
        # 按投入资金加权的策略收益率（sum(profit)/sum(buy_price*shares)），
        # 避免"每笔收益率简单相加"产生误导性的巨大数值
        row["_profit_sum"] = row.get("_profit_sum", 0.0) + float(t.get("profit") or 0.0)
        row["_cost_sum"] = row.get("_cost_sum", 0.0) + float(t.get("buy_price") or 0.0) * float(t.get("shares") or 0)
        if float(t.get("return_pct") or 0.0) > 0:
            row["win_rate_pct"] = (
                (row["win_rate_pct"] * (row["trade_count"] - 1) + 100.0)
                / row["trade_count"]
            )
        else:
            row["win_rate_pct"] = (
                (row["win_rate_pct"] * (row["trade_count"] - 1)) / row["trade_count"]
            )
    for s in skips:
        strat = s.get("strategy") or "未知"
        row = by_strategy.setdefault(
            strat,
            {
                "strategy": strat,
                "trade_count": 0,
                "skip_count": 0,
                "win_rate_pct": 0.0,
                "total_return_pct": 0.0,
                "annual_return_pct": metrics.get("annual_return_pct") or 0.0,
                "max_drawdown_pct": metrics.get("max_drawdown_pct") or 0.0,
                "sharpe": metrics.get("sharpe") or 0.0,
                "final_cash": metrics.get("final_cash") or 0.0,
                "initial_cash": metrics.get("initial_cash") or 0.0,
                "open_positions": 0,
                "capital_mode": "",
                "fixed_cash_per_trade": 0.0,
                "start_date": "",
                "end_date": "",
            },
        )
        row["skip_count"] += 1
    for row in by_strategy.values():
        cost = row.pop("_cost_sum", 0.0)
        profit = row.pop("_profit_sum", 0.0)
        row["total_return_pct"] = (profit / cost * 100.0) if cost > 0 else 0.0
    return list(by_strategy.values())


def _backtest_artifacts(key: str) -> list[dict]:
    bt_dir = _executions_root() / key / "backtest"
    artifacts = []
    if bt_dir.is_dir():
        for f in sorted(bt_dir.iterdir()):
            if f.is_file():
                artifacts.append(_artifact_row(key, f, scope="execution"))
    return artifacts


def _backtest_selection_keys(key: str) -> list[str]:
    import sqlite3

    db = _storage_root() / "app.db"
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT source_execution_key FROM execution_links "
            "WHERE target_execution_key = ? AND link_type = 'backtest_uses_selection'",
            (key,),
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    return [r[0] for r in rows]


def backtest_result_payload(key: str, include_report: bool = False) -> dict:
    bt_dir = _executions_root() / key / "backtest"
    result = _read_json(bt_dir / "result.json", {})
    metrics = _read_json(bt_dir / "metrics.json", {})
    equity = result.get("equity_curve", [])

    config = _backtest_config(key)
    start_date = equity[0].get("date") if equity else None
    end_date = equity[-1].get("date") if equity else None
    strategies = sorted({t.get("strategy") for t in result.get("trades", [])} | {s.get("strategy") for s in result.get("skips", [])})
    selection_keys = _backtest_selection_keys(key)

    summaries = _backtest_summary(key, result, metrics)
    for row in summaries:
        row["capital_mode"] = config["capital_mode"]
        row["fixed_cash_per_trade"] = config["cash_per_trade"]
        row["start_date"] = start_date or ""
        row["end_date"] = end_date or ""

    run = {
        "execution_key": key,
        "created_at": _file_mtime_iso(bt_dir) if bt_dir.exists() else None,
        "finished_at": _file_mtime_iso(bt_dir) if bt_dir.exists() else None,
        "start_date": start_date,
        "end_date": end_date,
        "strategies": strategies,
        "capital_mode": config["capital_mode"],
        "cash_per_trade": config["cash_per_trade"],
        "trade_rule": {
            **config.get("execution", {}),
            "trade_strategy": config.get("trade_strategy"),
        },
        "strategy_snapshots": [],
        "status": "success",
        "summary": summaries,
        "selection_execution_keys": selection_keys,
        "selection_from": None,
        "selection_to": None,
    }

    if not include_report:
        return run

    # 交易/跳过明细补股票名称与板块（前端"名称"列）
    meta_map = _stock_meta_map()
    trades = _enrich_stock_info(result.get("trades", []), meta_map)
    skips = _enrich_stock_info(result.get("skips", []), meta_map)

    return {
        "result": run,
        "artifacts": _backtest_artifacts(key),
        "trades": trades,
        "equity": result.get("equity_curve", []),
        "skips": skips,
    }


def list_backtest_results_payload(limit: int | None = None) -> dict:
    root = _executions_root()
    results = []
    if root.is_dir():
        for exec_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if limit is not None and len(results) >= limit:
                break
            if not (exec_dir / "backtest" / "result.json").exists():
                continue
            try:
                results.append(backtest_result_payload(exec_dir.name))
            except Exception:
                continue
    return {"results": results}


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------


_market_status_cache: dict = {"at": 0.0, "payload": None}
_MARKET_STATUS_TTL = 30.0
_stock_meta_cache: dict = {"at": 0.0, "by_code": {}}
_STOCK_META_TTL = 60.0


def _stock_meta_by_code() -> dict[str, tuple[str, str]]:
    """Map code -> (name, industry) from stock_meta.parquet, cached."""
    import time as _time

    now = _time.time()
    if _stock_meta_cache["by_code"] and now - _stock_meta_cache["at"] < _STOCK_META_TTL:
        return _stock_meta_cache["by_code"]

    by_code: dict[str, tuple[str, str]] = {}
    meta_file = _storage_root() / "market" / "stock_meta.parquet"
    if meta_file.exists():
        try:
            import polars as pl

            df = pl.read_parquet(meta_file, columns=["code", "name", "industry"])
            for code, name, industry in df.iter_rows():
                by_code[str(code)] = (str(name or ""), str(industry or ""))
        except Exception:
            pass

    _stock_meta_cache["at"] = now
    _stock_meta_cache["by_code"] = by_code
    return by_code


def _compute_market_status() -> dict:
    bars_dir = _storage_root() / "market" / "bars"
    meta_file = _storage_root() / "market" / "stock_meta.parquet"

    if bars_dir.is_dir():
        files = sorted(bars_dir.glob("*.parquet"))
        count = len(files)
        latest_date = None
        if files:
            try:
                import polars as pl

                max_date = (
                    pl.scan_parquet([str(p) for p in files])
                    .select(pl.col("date").max())
                    .collect()[0, 0]
                )
                latest_date = str(max_date) if max_date is not None else None
            except Exception:
                latest_date = None
    else:
        count = 0
        latest_date = None

    return {
        "data_dir": str(bars_dir),
        "stocklist": str(meta_file) if meta_file.exists() else "",
        "stock_count": count,
        "local_file_count": count,
        "latest_date": latest_date,
    }


def market_status_payload() -> dict:
    """Market status with a short TTL cache (bar files change only on sync)."""
    import time as _time

    now = _time.time()
    if _market_status_cache["payload"] is not None and now - _market_status_cache["at"] < _MARKET_STATUS_TTL:
        return _market_status_cache["payload"]

    payload = _compute_market_status()
    _market_status_cache["at"] = now
    _market_status_cache["payload"] = payload
    return payload


_trading_dates_cache: dict = {"at": 0.0, "payload": None}
_TRADING_DATES_TTL = 30.0


def trading_dates_payload(start: str | None = None, end: str | None = None) -> dict:
    """Trading dates with a short TTL cache (bar files change only on sync)."""
    import time as _time

    now = _time.time()
    if (
        start is None
        and end is None
        and _trading_dates_cache["payload"] is not None
        and now - _trading_dates_cache["at"] < _TRADING_DATES_TTL
    ):
        return _trading_dates_cache["payload"]

    from datetime import date

    from trendradar.domain.market.data_store import LocalParquetMarketStore

    bars_dir = _storage_root() / "market" / "bars"
    market_store = LocalParquetMarketStore(bars_dir)
    try:
        calendar = market_store.get_calendar()
    except Exception:
        calendar = []

    all_dates = sorted(calendar)
    if start:
        s = date.fromisoformat(start)
        all_dates = [d for d in all_dates if d >= s]
    if end:
        e = date.fromisoformat(end)
        all_dates = [d for d in all_dates if d <= e]

    payload = {
        "from": str(all_dates[0]) if all_dates else None,
        "to": str(all_dates[-1]) if all_dates else None,
        "count": len(all_dates),
        "dates": [str(d) for d in all_dates],
    }
    if start is None and end is None:
        _trading_dates_cache["at"] = now
        _trading_dates_cache["payload"] = payload
    return payload


def invalidate_market_status_cache() -> None:
    """任何同步终态/确认入账后调用（30 s TTL 对面板太慢）。"""
    _market_status_cache["at"] = 0.0
    _market_status_cache["payload"] = None
    _trading_dates_cache["at"] = 0.0
    _trading_dates_cache["payload"] = None


# ---------------------------------------------------------------------------
# execution / console
# ---------------------------------------------------------------------------


_TRADE_STRATEGY_EXECUTION = {
    "long_term_bull_bear_stop": {
        "force_sell_on_two_day_close_below_long_term_bull_bear_line": True,
    },
    "ten_day_low_stop": {
        "close_below_recent_low_stop_window": 10,
    },
    "ultra_short": {
        "entry_on_signal_day": True,
        "entry_at_close": True,
        "fixed_hold_n_days": 1,
    },
}


def _trade_strategy_execution(trade_strategy: str | None) -> dict:
    """Map a UI trade-strategy value to backend execution params (超短线等）。"""
    return dict(_TRADE_STRATEGY_EXECUTION.get(trade_strategy or "", {}))


def submit_execution_payload(executor, market_store, store, request: dict) -> dict:
    """Dispatch a frontend-style {type, params} submission.

    Returns the frontend ExecutionSubmitResponse shape (execution_id,
    execution_type, status, console_url, ...).
    """
    from trendradar.app.jobs.context import JobContext
    from trendradar.app.services.backtest_service import (
        submit_backtest,
        submit_selection_backtest,
    )
    from trendradar.app.services.market_service import submit_market_sync
    from trendradar.app.services.selection_service import (
        submit_batch_selection,
        submit_selection,
    )

    from trendradar.domain.signal.repository import SignalRepository
    from trendradar.infrastructure.storage.artifact_store import ArtifactStore

    jtype = request.get("type")
    params = dict(request.get("params") or {})
    for key in ("start_date", "end_date", "codes", "groups", "strategies"):
        if request.get(key) is not None and key not in params:
            params[key] = request[key]

    job_id = None
    job_type = None
    repo = SignalRepository(ArtifactStore(_storage_root()))

    if jtype in (None, "selection_latest", "selection_single", "selection_batch"):
        if jtype == "selection_latest":
            calendar = market_store.get_calendar() if hasattr(market_store, "get_calendar") else []
            latest = str(calendar[-1]) if calendar else None
            start = end = latest
        else:
            start = params.get("date") or params.get("start_date") or params.get("from")
            end = params.get("date") or params.get("end_date") or params.get("to")
        sel_params = {
            "start_date": start,
            "end_date": end,
            "codes": params.get("codes"),
            "groups": params.get("groups"),
            "strategies": _strategies_names_to_ids(params.get("strategies")),
        }
        if jtype == "selection_batch":
            job_id = submit_batch_selection(executor, market_store, sel_params, store)
            job_type = "batch_selection"
        else:
            job_id = submit_selection(executor, market_store, sel_params, store)
            job_type = "selection"
    elif jtype == "backtest_from_selection":
        keys = params.get("selection_execution_keys") or []
        if not keys:
            raise ValueError("selection_execution_keys is required")
        execution_key = keys[0]
        signal_set = repo.load(execution_key)
        if signal_set is None:
            raise ValueError(f"信号集不存在: {execution_key}")
        from datetime import date
        from trendradar.app.services.backtest_service import (
            validate_backtest_prerequisites,
        )

        dates = [date.fromisoformat(d) for d in trading_dates_payload()["dates"]]
        execution = _trade_strategy_execution(params.get("trade_strategy"))
        reasons = validate_backtest_prerequisites(
            signal_set,
            dates,
            fixed_hold_n_days=execution.get("fixed_hold_n_days", 5),
            entry_on_signal_day=execution.get("entry_on_signal_day", False),
        )
        if reasons:
            raise ValueError("；".join(reasons))

        job_id = submit_backtest(
            executor,
            market_store,
            repo,
            {
                "execution_key": execution_key,
                "capital": {
                    "mode": params.get("mode", "unlimited_cash"),
                    "fixed_cash_per_trade": params.get("cash_per_trade", 50000),
                },
                "execution": execution,
                "trade_strategy": params.get("trade_strategy"),
            },
        )
        job_type = "backtest"
    elif jtype == "selection_backtest":
        job_id = submit_selection_backtest(
            executor,
            market_store,
            repo,
            {
                "start_date": params.get("from") or params.get("start_date"),
                "end_date": params.get("to") or params.get("end_date"),
                "groups": params.get("groups"),
                "strategies": _strategies_names_to_ids(params.get("strategies")),
                "backtest": {
                    "capital": {
                        "mode": params.get("mode", "unlimited_cash"),
                        "fixed_cash_per_trade": params.get("cash_per_trade", 50000),
                    },
                    "execution": _trade_strategy_execution(params.get("trade_strategy")),
                },
                "trade_strategy": params.get("trade_strategy"),
            },
        )
        job_type = "selection_backtest"
    elif jtype == "market_data_sync":
        job_id = submit_market_sync(
            executor,
            {
                "start_date": params.get("start") or params.get("start_date"),
                "end_date": params.get("end") or params.get("end_date"),
                "codes": params.get("codes"),
                "force": params.get("force", False),
                "exclude_boards": params.get("exclude_boards"),
            },
        )
        job_type = "market_sync"
    else:
        raise ValueError(f"Unknown execution type: {jtype!r}")

    return {
        "execution_id": job_id,
        "execution_type": job_type,
        "type": jtype or "selection",
        "status": "submitted",
        "progress_current": 0,
        "progress_total": 0,
        "progress_message": "",
        "error_message": None,
        "result_url": None,
        "console_url": f"/console/{job_id}",
        "created_at": _now_iso(),
    }


def parse_progress(logs: list[str]) -> dict:
    current = 0
    total = 0
    message = ""
    for line in logs:
        m = _PROGRESS_RE.search(line)
        if m:
            current = int(m.group(1))
            total = int(m.group(2))
            message = m.group(4).strip()
    return {
        "progress_current": current,
        "progress_total": total,
        "progress_message": message,
    }


def result_url_for(job_type: str, status: str, job_id: str) -> str | None:
    if status != "success":
        return None
    if job_type in ("selection", "batch_selection", "selection_latest", "selection_single", "selection_batch"):
        return f"/selections/{job_id}"
    if job_type in ("backtest", "backtest_from_selection", "selection_backtest"):
        return f"/backtests/{job_id}"
    return None


def console_payload(executor, job_id: str, offset: int = 0) -> dict:
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.app.jobs.persistence import JobStore

    state = executor.get_state(job_id)
    if state.get("status") == "unknown":
        raise KeyError(job_id)

    store = JobStore(StorageConnection(runtime_root() / "storage").db_path)
    all_logs = store.get_logs(job_id)
        # 尾随换行：前端轮询用 pendingText 拼接下一批，缺了它会把相邻两条日志粘成一行
    text = "\n".join(all_logs[offset:]) + "\n"
    progress = parse_progress(all_logs)

    status = state.get("status")
    job_type = state.get("job_type")
    running = status in ("queued", "running", "cancelling")

    execution = {
        "execution_id": job_id,
        "execution_type": job_type,
        "type": job_type,
        "status": status,
        "progress_current": progress["progress_current"],
        "progress_total": progress["progress_total"],
        "progress_message": progress["progress_message"],
        "error_message": state.get("error"),
        "result_url": result_url_for(job_type, status, job_id),
        "created_at": state.get("created_at"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
    }

    return {
        "execution": execution,
        "offset": offset + len(all_logs[offset:]),
        "text": text,
        "more": running or offset + len(all_logs[offset:]) < len(all_logs),
    }


# ---------------------------------------------------------------------------
# bulk delete
# ---------------------------------------------------------------------------


def _referencing_backtests(selection_key: str) -> list[str]:
    """execution_links backtest keys that consume this selection."""
    import sqlite3

    db = _storage_root() / "app.db"
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT target_execution_key FROM execution_links "
            "WHERE source_execution_key = ? AND link_type = 'backtest_uses_selection'",
            (selection_key,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def bulk_delete_selections(execution_keys: list[str] | None = None) -> dict:
    import shutil

    root = _executions_root()
    if execution_keys is None:
        targets = [
            d
            for d in (root.iterdir() if root.is_dir() else [])
            if d.is_dir() and (d / "selection" / "signals.json").exists()
        ]
    else:
        targets = []
        for key in execution_keys:
            if "/" in key or "\\" in key or ".." in key:
                continue
            d = root / key
            if d.is_dir() and (d / "selection" / "signals.json").exists():
                targets.append(d)

    missing = [
        key
        for key in (execution_keys or [])
        if not (root / key).exists()
        or not (root / key / "selection" / "signals.json").exists()
    ]
    deleted = 0
    deleted = 0
    file_errors = []
    for d in targets:
        refs = _referencing_backtests(d.name)
        if refs:
            file_errors.append(
                f"选股 {d.name} 被回测 {', '.join(refs)} 引用，未删除"
            )
            continue
        try:
            shutil.rmtree(d)
            deleted += 1
        except Exception:
            pass
    return {
        "result_type": "selection",
        "requested": len(execution_keys) if execution_keys is not None else deleted,
        "deleted": deleted,
        "missing": missing,
        "file_errors": file_errors,
    }


def bulk_delete_backtests(execution_keys: list[str] | None = None) -> dict:
    """Delete backtest execution directories; shape mirrors selection deletes.

    None = clear every directory holding a backtest (no-body request).
    Keys = delete only those; entries that do not exist or are not a backtest
    are reported in ``missing`` (computed before deletion, so a deleted key is
    never misreported as missing).
    """
    import shutil

    root = _executions_root()

    def _is_backtest(d: Path) -> bool:
        return d.is_dir() and (d / "backtest" / "metrics.json").exists()

    if execution_keys is None:
        targets = [
            d
            for d in (root.iterdir() if root.is_dir() else [])
            if _is_backtest(d)
        ]
        missing = []
    else:
        targets = []
        missing = []
        for key in execution_keys:
            if "/" in key or "\\" in key or ".." in key or "." in key:
                missing.append(key)
                continue
            d = root / key
            if _is_backtest(d):
                targets.append(d)
            else:
                missing.append(key)

    deleted = 0
    for d in targets:
        try:
            shutil.rmtree(d)
            deleted += 1
        except Exception:
            pass
    return {
        "result_type": "backtest",
        "requested": len(execution_keys) if execution_keys is not None else deleted,
        "deleted": deleted,
        "missing": missing,
        "file_errors": [],
    }
