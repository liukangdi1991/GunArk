"""Presentation helpers: aggregate V2 runtime data into the frontend contract.

The Web workbench (frontend/src/types/*.ts) expects bare, rich response
objects — no {"data": ...} wrapper. These functions build those shapes from
the artifact files, SQLite metadata, and the strategy registry.
"""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime, timezone
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


def _rule_from_config_dict(cfg: dict, request: dict) -> dict:
    """从一份生效配置字典构建 trade_rule。两路（快照 / 回落重算）共用同一映射，
    避免逻辑分叉：execution 全量 + capital.lot_size + 整个 costs + position_limits 四键。

    trade_strategy 取自请求——它是请求数据，不随代码默认值漂移，request_json 里一直在。
    """
    rule = dict(cfg["execution"])
    rule["lot_size"] = cfg["capital"]["lot_size"]
    rule["costs"] = dict(cfg["costs"])
    # 持仓上限不进快照的话，"每天限开 5 只"的报告和不限的报告长得一模一样
    portfolio = cfg["portfolio"]
    rule["position_limits"] = {
        k: portfolio[k]
        for k in (
            "target_positions", "max_positions",
            "max_single_position_pct", "max_daily_new_positions",
        )
    }
    params = request.get("params") or {}
    rule["trade_strategy"] = params.get("trade_strategy") or request.get("trade_strategy")
    return rule


def _effective_trade_rule(request: dict, key: str) -> dict:
    """参数快照回答的是"这次实际按什么规则跑的"，不是"当时请求了什么"。

    优先读 worker 随产物固化的 `effective_config.json`——引擎真正用的那套配置。
    读不到（快照机制上线前的历史产物）才回落：把存下来的请求喂回 `_build_config`
    重算。回落不劣于现状（request_json 里显式设过的字段仍准），但吃了默认值的字段
    会随**今天**的默认值漂移，与产物自己的逐笔盈亏对不上——这正是 P1#2 的失真，
    新产物起不再发生（见 2026-09-07-effective-config-snapshot-design.md）。
    """
    snapshot = _read_json(_executions_root() / key / "backtest" / "effective_config.json")
    if snapshot is None:
        from dataclasses import asdict

        from trendradar.app.services.backtest_service import _build_config

        bt_request = request.get("backtest") or request
        snapshot = asdict(_build_config(bt_request))
    return _rule_from_config_dict(snapshot, request)


def _db_utc_to_iso(value: str | None) -> str | None:
    """jobs/executions 里的时间戳是无时区的 UTC 字符串，有两种写法：
    SQLite `CURRENT_TIMESTAMP` 的 `"YYYY-MM-DD HH:MM:SS"` 和代码写的 `"...T..."`。

    必须补上 `+00:00` 再返回：裸字符串到了前端 `dayjs()` 会被当**本地**时间解析，
    非 UTC 时区的用户看到的完成时间是错的。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _backtest_job_row(key: str) -> dict:
    import sqlite3

    db = _storage_root() / "app.db"
    columns = ("request_json", "created_at", "finished_at", "status")
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {', '.join(columns)} FROM jobs WHERE job_id = ?", (key,)
        ).fetchone()
        conn.close()
    except Exception:
        return {}
    return dict(row) if row else {}


def _backtest_config(key: str) -> dict:
    """Read the original request (mode/cash) and timing from the jobs table."""
    job = _backtest_job_row(key)
    request = json.loads(job["request_json"]) if job.get("request_json") else {}
    params = request.get("params") or {}
    # capital 可能位于顶层（backtest_from_selection）或 backtest 下（selection_backtest）
    capital = request.get("capital") or (request.get("backtest") or {}).get("capital") or {}
    return {
        "capital_mode": params.get("mode") or capital.get("mode", "unlimited_cash"),
        "cash_per_trade": params.get("cash_per_trade") or capital.get("fixed_cash_per_trade", 50000),
        "trade_rule": _effective_trade_rule(request, key),
        "created_at": _db_utc_to_iso(job.get("created_at")),
        "finished_at": _db_utc_to_iso(job.get("finished_at")),
        "status": job.get("status"),
    }


def _backtest_selection_sources(key: str, linked_keys: list[str]) -> list[str]:
    """回测的选股来源：血缘链接指向的选股，或组合管线自己目录下的 `selection/`。

    `selection_backtest` 里选股与回测共用 job_id、不写血缘链接，所以查不到链接时
    还要看同目录。
    """
    if linked_keys:
        return linked_keys
    return [key] if (_executions_root() / key / "selection" / "signals.json").exists() else []


def _selection_provenance(selection_keys: list[str]) -> dict:
    """从来源选股的 signals.json / lineage.json 还原"这批信号是怎么选出来的"。

    多个来源时日期取并集首尾、快照按策略名去重——报告要回答的是覆盖面，
    不是每个来源各跑了一遍。
    """
    from_dates: list[str] = []
    to_dates: list[str] = []
    snapshots: list[dict] = []
    seen: set[str] = set()

    for sel_key in selection_keys:
        sel_dir = _executions_root() / sel_key / "selection"
        signals = _read_json(sel_dir / "signals.json", {})
        lineage = _read_json(sel_dir / "lineage.json", {})
        if signals.get("signal_from"):
            from_dates.append(signals["signal_from"])
        if signals.get("signal_to"):
            to_dates.append(signals["signal_to"])
        for snap in _selection_snapshots(lineage):
            if snap["name"] in seen:
                continue
            seen.add(snap["name"])
            snapshots.append(snap)

    return {
        "selection_from": min(from_dates) if from_dates else None,
        "selection_to": max(to_dates) if to_dates else None,
        "strategy_snapshots": snapshots,
    }


def _backtest_summary(key: str, result: dict, metrics: dict) -> list[dict]:
    trades = result.get("trades", [])
    skips = result.get("skips", [])
    open_positions = result.get("open_positions", [])
    by_strategy: dict[str, dict] = {}

    def row_for(strat: str) -> dict:
        return by_strategy.setdefault(
            strat,
            {
                "strategy": strat,
                "trade_count": 0,
                "skip_count": 0,
                "win_rate_pct": 0.0,
                "total_return_pct": 0.0,
                "realized_profit_sum": 0.0,
                "invested_notional_sum": 0.0,
                "unrealized_pnl": 0.0,
                # 组合级净值原样透传：unlimited_cash 模式下它们是 N-A，
                # 用 `or 0.0` 兜底等于把"没有定义"改成"不赚不亏"
                "annual_return_pct": metrics.get("annual_return_pct"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "final_cash": metrics.get("final_cash"),
                "initial_cash": metrics.get("initial_cash"),
                "open_positions": 0,
                "capital_mode": "",
                "fixed_cash_per_trade": 0.0,
                "start_date": "",
                "end_date": "",
            },
        )

    for t in trades:
        row = row_for(t.get("strategy") or "未知")
        row["trade_count"] += 1
        # 按投入资金加权的策略收益率（sum(profit)/sum(buy_price*shares)），
        # 避免"每笔收益率简单相加"产生误导性的巨大数值
        row["realized_profit_sum"] += float(t.get("profit") or 0.0)
        row["invested_notional_sum"] += (
            float(t.get("buy_price") or 0.0) * float(t.get("shares") or 0)
        )
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
        row_for(s.get("strategy") or "未知")["skip_count"] += 1
    for p in open_positions:
        row = row_for(p.get("strategy") or "未知")
        row["open_positions"] += 1
        row["unrealized_pnl"] += float(p.get("unrealized_pnl") or 0.0)
    for row in by_strategy.values():
        invested = row["invested_notional_sum"]
        # 一笔都没成交 ⇒ 收益率无定义，不是 0.00%
        row["total_return_pct"] = (
            (row["realized_profit_sum"] / invested * 100.0) if invested > 0 else None
        )
    return list(by_strategy.values())


def _backtest_window(result: dict, equity: list[dict]) -> tuple[str | None, str | None]:
    """回测区间。曲线的收发两端原来就是它，但 unlimited_cash 模式不产曲线。"""
    if equity:
        return equity[0].get("date"), equity[-1].get("date")
    dates: list[str] = []
    for t in result.get("trades", []):
        dates += [d for d in (t.get("buy_date"), t.get("sell_date")) if d]
    for p in result.get("open_positions", []):
        dates += [d for d in (p.get("buy_date"),
                              p.get("mark_date") or p.get("target_sell_date")) if d]
    dates += [s.get("buy_date") for s in result.get("skips", []) if s.get("buy_date")]
    return (min(dates), max(dates)) if dates else (None, None)


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


def _strategy_display_name(strategy_id) -> str | None:
    """回测产物里只存 strategy id（机器键），展示名在提交时从注册表现查。

    查不到返回 None 而不是回落 id：前端 `strategy_name ?? strategy` 自己回落，
    已删除策略的历史报告因此仍能渲染，只是显示英文 id。
    """
    from trendradar.domain.strategy.registry import get

    defn = get(strategy_id or "")
    return defn.name if defn else None


def _with_strategy_names(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["strategy_name"] = _strategy_display_name(row.get("strategy"))
    return rows


def backtest_result_payload(key: str, include_report: bool = False) -> dict:
    bt_dir = _executions_root() / key / "backtest"
    result = _read_json(bt_dir / "result.json", {})
    metrics = _read_json(bt_dir / "metrics.json", {})
    equity = result.get("equity_curve", [])

    config = _backtest_config(key)
    start_date, end_date = _backtest_window(result, equity)
    strategies = sorted(
        {t.get("strategy") for t in result.get("trades", [])}
        | {s.get("strategy") for s in result.get("skips", [])}
        | {p.get("strategy") for p in result.get("open_positions", [])}
    )
    selection_keys = _backtest_selection_keys(key)
    provenance = _selection_provenance(_backtest_selection_sources(key, selection_keys))
    mtime_iso = _file_mtime_iso(bt_dir) if bt_dir.exists() else None

    summaries = _with_strategy_names(_backtest_summary(key, result, metrics))
    for row in summaries:
        row["capital_mode"] = config["capital_mode"]
        row["fixed_cash_per_trade"] = config["cash_per_trade"]
        row["start_date"] = start_date or ""
        row["end_date"] = end_date or ""

    run = {
        "execution_key": key,
        "created_at": config["created_at"] or mtime_iso,
        "finished_at": config["finished_at"] or mtime_iso,
        "start_date": start_date,
        "end_date": end_date,
        "strategies": [_strategy_display_name(sid) or sid for sid in strategies],
        "capital_mode": config["capital_mode"],
        "cash_per_trade": config["cash_per_trade"],
        "trade_rule": config["trade_rule"],
        # 产物存在即视为跑完：查不到 jobs 行的历史产物回落 success，与时间字段的 mtime 回落同理
        "status": config["status"] or "success",
        "strategy_snapshots": provenance["strategy_snapshots"],
        "summary": summaries,
        "selection_execution_keys": selection_keys,
        "selection_from": provenance["selection_from"],
        "selection_to": provenance["selection_to"],
    }

    if not include_report:
        return run

    # 交易/跳过明细补股票名称与板块（前端"名称"列）
    meta_map = _stock_meta_map()
    trades = _with_strategy_names(_enrich_stock_info(result.get("trades", []), meta_map))
    skips = _with_strategy_names(_enrich_stock_info(result.get("skips", []), meta_map))
    open_positions = _with_strategy_names(
        _enrich_stock_info(result.get("open_positions", []), meta_map)
    )

    return {
        "result": run,
        "artifacts": _backtest_artifacts(key),
        "trades": trades,
        "equity": result.get("equity_curve", []),
        "skips": skips,
        "open_positions": open_positions,
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
    from datetime import datetime

    from trendradar.domain.market.sync.planner import stale_days
    from trendradar.domain.market.sync.spec import (
        BASELINE_START,
        SHANGHAI,
        latest_tradeable_day,
    )
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from trendradar.infrastructure.tushare.writer import readback_calendar

    store_root = storage_root()
    bars_dir = store_root / "market" / "bars"
    meta_file = store_root / "market" / "stock_meta.parquet"
    sync = SyncStore(store_root)

    now_cn = datetime.now(SHANGHAI)
    today = now_cn.date()

    # ① 日历
    calendar_days = sync.calendar_days()
    max_cal = max(calendar_days) if calendar_days else None
    cal_job = _latest_job("market_calendar_sync")
    calendar = {
        "status": cal_job["status"] if cal_job else None,
        "max_trade_date": str(max_cal) if max_cal else None,
        "covers_today": bool(max_cal and max_cal >= today),
        "job_id": cal_job["job_id"] if cal_job else None,
    }

    # ② 新鲜度（基于读回实测日历，不信任 done_days）
    latest = latest_tradeable_day(calendar_days, now_cn) if calendar_days else None
    readback = readback_calendar(bars_dir) if bars_dir.is_dir() else set()
    trusted = max((d for d in readback if latest is None or d <= latest), default=None)
    freshness = {
        "trusted_through": str(trusted) if trusted else None,
        "latest_tradeable": str(latest) if latest else None,
        "stale_days": stale_days(calendar_days, readback, latest) if calendar_days else None,
        "total_missing_days": (
            len([d for d in calendar_days
                 if latest is not None and BASELINE_START <= d <= latest and d not in readback])
            if latest is not None else None
        ),
    }

    # ③ 最近一次行情同步
    bars_job = _latest_job("market_bars_sync")
    bars_sync = {
        "status": bars_job["status"] if bars_job else None,
        "finished_at": bars_job["finished_at"] if bars_job else None,
        "error_message": bars_job["error_message"] if bars_job else None,
        "job_id": bars_job["job_id"] if bars_job else None,
    }

    # ④ 个股覆盖（明细取前 10 条）
    skipped = sync.skipped_rows()
    coverage = {
        "missing_codes": len(skipped),
        "skipped": [
            {"code": r["code"], "attempts": r["attempts"], "last_error": r["last_error"]}
            for r in skipped[:10]
        ],
    }

    # ⑤ 一致性（marker_mismatch = 账本有勾但读回无此日）
    done_days = sync.done_days()
    consistency = {
        "ledger_suspect": sync.ledger_suspect(),
        "marker_mismatch": bool(done_days and done_days - readback),
        "doubtful_days": [d.isoformat() for d in sync.doubtful_days()],
    }

    # ⑥ 本地存储（沿用现状数字，供面板基线行）
    file_count = len(list(bars_dir.glob("*.parquet"))) if bars_dir.is_dir() else 0
    storage = {
        "data_dir": str(bars_dir),
        "stocklist": str(meta_file) if meta_file.exists() else "",
        "stock_count": file_count,
        "local_file_count": file_count,
        "latest_date": str(max(readback)) if readback else None,
    }

    return {
        "calendar": calendar,
        "freshness": freshness,
        "bars_sync": bars_sync,
        "coverage": coverage,
        "consistency": consistency,
        "storage": storage,
    }


def _latest_job(job_type: str) -> dict | None:
    import sqlite3

    db = _storage_root() / "app.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT job_id, status, finished_at, error_message FROM jobs "
            "WHERE job_type = ? ORDER BY id DESC LIMIT 1",
            (job_type,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


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
    from trendradar.app.services.selection_service import (
        submit_batch_selection,
        submit_selection,
        validate_selection_request,
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
        validate_selection_request(sel_params, store)
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
        bt_params = {
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
        }
        validate_selection_request(bt_params, store)
        job_id = submit_selection_backtest(
            executor,
            market_store,
            repo,
            bt_params,
        )
        job_type = "selection_backtest"
    elif jtype == "market_bars_sync":
        from trendradar.app.services.market_service import submit_market_bars_sync
        job_id = submit_market_bars_sync(
            executor,
            {
                "force": params.get("force", False),
                "exclude_boards": params.get("exclude_boards") or [],
                "accept_partial_baseline": params.get("accept_partial_baseline", False),
            },
        )
        job_type = "market_bars_sync"
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
