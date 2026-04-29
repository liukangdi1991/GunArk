from __future__ import annotations

import json
import re
import time
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import polars as pl

from select_stock import (
    build_strategy_runner,
    load_data_table,
    load_default_strategy_aliases,
    load_strategies_from_config,
    table_to_data_dict,
)
from web.core.config import ROOT, storage
from web.schemas.selection import BatchSelectionRequest, SelectionRequest
from web.services import market_service
from web.services.strategy_service import list_strategies


DATA_DIR = ROOT / "db"
CONFIG_PATH = ROOT / "configs.json"
STOCKLIST = ROOT / "stocklist.csv"
ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


def create_selection(
    payload: SelectionRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    date_obj = _parse_date(payload.date) if payload.date else _get_latest_trade_date()
    if date_obj is None:
        raise ValueError("未找到可用行情交易日")

    if progress:
        progress(0, 0, "正在加载行情数据")
    if log:
        log(f"加载行情数据，选股日期: {date_obj.isoformat()}")
    data_table = load_data_table(str(DATA_DIR), payload.tickers)
    if data_table.is_empty():
        raise ValueError("无行情数据，请先拉取行情")
    if log:
        stock_count = data_table["code"].n_unique()
        log(f"行情加载完成: {stock_count} 只股票")

    strategies = load_strategies_from_config(CONFIG_PATH)
    strategy_names = _resolve_strategy_names(payload.strategies, strategies)
    if not strategy_names:
        raise ValueError("没有可运行的选股策略")
    if progress:
        progress(0, len(strategy_names), f"准备执行 {len(strategy_names)} 个选股策略")
    if log:
        log(f"策略范围: {', '.join(strategy_names)}")

    result = _run_selection_for_date(
        date_obj=date_obj,
        data_table=data_table,
        strategies=strategies,
        strategy_names=strategy_names,
        progress=progress,
        log=log,
    )
    return result


def create_batch_selection(
    payload: BatchSelectionRequest,
    *,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    if progress:
        progress(0, 0, "正在加载行情数据")
    if log:
        log("加载行情数据")
    data_table = load_data_table(str(DATA_DIR), None)
    if data_table.is_empty():
        raise ValueError("无行情数据，请先拉取行情")

    strategies = load_strategies_from_config(CONFIG_PATH)
    strategy_names = _resolve_strategy_names(payload.strategies, strategies)
    if not strategy_names:
        raise ValueError("没有可运行的选股策略")

    dates, range_label, request_meta = _collect_batch_trade_dates(payload)
    if not dates:
        raise ValueError(f"指定日期范围内无交易数据: {range_label}")

    total = len(dates)
    if progress:
        progress(0, total, f"准备执行 {total} 个交易日")
    if log:
        log(
            f"批量选股开始: {range_label}，实际交易日 {total} 个，"
            f"范围 {dates[0].isoformat()} ~ {dates[-1].isoformat()}"
        )

    results = []
    for index, day in enumerate(dates, 1):
        date_text = day.isoformat()
        if progress:
            progress(index - 1, total, f"正在执行 {date_text}")
        if log:
            log(f"{date_text} 开始")

        result = _run_selection_for_date(
            date_obj=day,
            data_table=data_table,
            strategies=strategies,
            strategy_names=strategy_names,
        )
        results.append(result)

        selected_count = sum(int(item.get("count") or 0) for item in result.get("summary", []))
        if log:
            for item in result.get("summary", []):
                log(f"{date_text} {item.get('strategy')} 选出 {item.get('count')} 只")
            log(f"{date_text} 完成，选出 {selected_count} 条")
        if progress:
            progress(index, total, f"{date_text} 完成")

    return {
        **request_meta,
        "trade_days": len(dates),
        "trade_from": dates[0].isoformat() if dates else None,
        "trade_to": dates[-1].isoformat() if dates else None,
        "results": results,
    }


def _run_selection_for_date(
    *,
    date_obj: date,
    data_table: pl.DataFrame,
    strategies: dict[str, dict[str, Any]],
    strategy_names: list[str],
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    data_dict_cache = None

    def get_data_dict():
        nonlocal data_dict_cache
        if data_dict_cache is None:
            data_dict_cache = table_to_data_dict(data_table)
        return data_dict_cache

    stock_meta = _load_stock_meta()
    all_results: dict[str, dict[str, Any]] = {}
    pick_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    total_start = time.time()
    date_text = date_obj.isoformat()

    total_strategies = len(strategy_names)
    for index, strategy_name in enumerate(strategy_names, 1):
        selector = strategies[strategy_name]["selector"]
        runner = build_strategy_runner(selector)
        if progress:
            progress(index - 1, total_strategies, f"正在执行 {strategy_name}")
        if log:
            log(f"{date_text} {strategy_name} 开始")
        start = time.time()
        picks = runner.run_selection(
            date_obj=date_obj,
            data_table=data_table,
            get_data_dict=get_data_dict,
        )
        elapsed = time.time() - start
        if log:
            log(f"{date_text} {strategy_name} 完成，选出 {len(picks)} 只，耗时 {elapsed:.3f}s")
        if progress:
            progress(index, total_strategies, f"{strategy_name} 完成")

        all_results[strategy_name] = {
            "date": date_text,
            "stocks": picks,
            "count": len(picks),
        }
        summaries.append(
            {
                "strategy": strategy_name,
                "date": date_text,
                "count": len(picks),
                "elapsed_seconds": elapsed,
            }
        )
        for code in picks:
            normalized_code = str(code).zfill(6)
            pick_rows.append(
                {
                    "strategy": strategy_name,
                    "date": date_text,
                    "code": normalized_code,
                    "name": stock_meta.get(normalized_code, {}).get("name", ""),
                    "industry": stock_meta.get(normalized_code, {}).get("industry", ""),
                }
        )

    execution_key = _build_execution_key(date_obj, strategy_names)
    object_dir = storage.objects_root / "executions" / execution_key / "selection"
    object_dir.mkdir(parents=True, exist_ok=True)

    picks_path = object_dir / "picks.parquet"
    signals_path = object_dir / "signals.json"
    log_path = object_dir / "log.txt"
    _write_signals_json(signals_path, all_results)
    signal_file = str(signals_path)
    if log:
        log(f"结果 JSON 已保存: {signal_file}")
    _write_picks_parquet(picks_path, execution_key, pick_rows)
    log_path.write_text(
        _build_log_text(
            execution_key=execution_key,
            date_text=date_text,
            strategy_names=strategy_names,
            summaries=summaries,
            elapsed=time.time() - total_start,
            signal_file=signal_file,
        ),
        encoding="utf-8",
    )

    snapshots = _strategy_snapshots(strategy_names)
    storage.record_selection_result(
        run_id=execution_key,
        selection_date=date_text,
        strategies=strategy_names,
        strategy_snapshots=snapshots,
        data_dir=str(DATA_DIR),
        signal_file=signal_file,
        summaries=summaries,
        object_dir=object_dir,
    )
    storage.register_artifact(
        run_type="selection",
        run_id=execution_key,
        artifact_type="signals_json",
        path=signals_path,
        mime_type="application/json; charset=utf-8",
    )
    storage.register_artifact(
        run_type="selection",
        run_id=execution_key,
        artifact_type="picks_parquet",
        path=picks_path,
        mime_type="application/vnd.apache.parquet",
    )
    storage.register_artifact(
        run_type="selection",
        run_id=execution_key,
        artifact_type="log_txt",
        path=log_path,
        mime_type="text/plain; charset=utf-8",
    )
    if log:
        log(f"选股记录已保存: {execution_key}")

    return {
        "execution_key": execution_key,
        "selection_date": date_text,
        "strategies": strategy_names,
        "summary": summaries,
        "signal_file": signal_file,
    }


def list_selections(limit: int = 50) -> dict[str, Any]:
    return {"results": storage.list_selection_results(limit=limit)}


def get_selection(execution_key: str) -> dict[str, Any] | None:
    result = storage.get_selection_result(execution_key)
    if result is None:
        return None
    return {
        "result": result,
        "artifacts": storage.list_artifacts(execution_key, run_type="selection"),
        "picks": _read_picks(execution_key),
    }


def delete_selection(execution_key: str) -> dict[str, Any]:
    return storage.delete_selection_results([execution_key])


def delete_selections(execution_keys: list[str] | None = None) -> dict[str, Any]:
    return storage.delete_selection_results(execution_keys)


def _resolve_strategy_names(
    requested: list[str] | None,
    strategies: dict[str, dict[str, Any]],
) -> list[str]:
    if requested:
        missing = [name for name in requested if name not in strategies]
        if missing:
            raise ValueError(f"策略不存在: {', '.join(missing)}")
        return [name for name in requested if str(name).strip()]

    default_aliases = load_default_strategy_aliases(CONFIG_PATH)
    return [name for name in default_aliases if name in strategies]


def _parse_date(value: str | None) -> date:
    if not value:
        latest = _get_latest_trade_date()
        if latest is None:
            raise ValueError("未找到可用行情交易日")
        return latest
    value = str(value).strip()
    if "-" in value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.strptime(value, "%Y%m%d").date()


def _get_latest_trade_date() -> date | None:
    try:
        df = pl.scan_parquet(str(DATA_DIR / "*.parquet")).select(
            pl.col("date").max().alias("max_date")
        ).collect()
        value = df["max_date"][0]
        return value if isinstance(value, date) else None
    except Exception:
        return None


def _load_all_trade_dates() -> list[date]:
    return market_service.load_trading_dates()


def _collect_batch_trade_dates(
    payload: BatchSelectionRequest,
) -> tuple[list[date], str, dict[str, Any]]:
    if payload.from_date or payload.to:
        if not payload.from_date or not payload.to:
            raise ValueError("日期区间批量选股需要同时提供 from 和 to")
        start = _parse_date(payload.from_date)
        end = _parse_date(payload.to)
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")

        dates = _collect_range_trade_dates(start, end)
        label = f"{start.isoformat()} ~ {end.isoformat()}"
        return dates, label, {"from": start.isoformat(), "to": end.isoformat()}

    if payload.month:
        dates = _collect_month_trade_dates(payload.month)
        return dates, payload.month, {"month": payload.month}

    raise ValueError("批量选股需要提供日期区间 from/to 或月份 month")


def _collect_range_trade_dates(start: date, end: date) -> list[date]:
    return [day for day in _load_all_trade_dates() if start <= day <= end]


def _collect_month_trade_dates(month_expr: str) -> list[date]:
    months = _parse_month_range(month_expr)
    all_dates = _load_all_trade_dates()
    selected: list[date] = []
    for month_start in months:
        if month_start.month == 12:
            month_end = date(month_start.year, 12, 31)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
        selected.extend(day for day in all_dates if month_start <= day <= month_end)
    return sorted(selected)


def _parse_month_range(value: str) -> list[date]:
    value = str(value).strip()
    if not re.fullmatch(r"\d{6}(-\d{6})?", value):
        raise ValueError("月份格式应为 YYYYMM 或 YYYYMM-YYYYMM")

    parts = value.split("-")
    start_ym = parts[0]
    end_ym = parts[-1]
    start = datetime.strptime(start_ym, "%Y%m")
    end = datetime.strptime(end_ym, "%Y%m")
    if start > end:
        raise ValueError("月份开始不能晚于结束")

    months: list[date] = []
    cur = start
    while cur <= end:
        months.append(cur.date())
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return months


def _build_execution_key(date_obj: date, strategy_names: list[str]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(strategy_names) == 1:
        suffix = _sanitize(strategy_names[0])
    else:
        suffix = f"{len(strategy_names)}strategies"
    date_part = date_obj.strftime("%Y%m%d")
    return f"{ts}_{date_part}_{suffix}" if suffix else f"{ts}_{date_part}"


def _sanitize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("_")


def _write_picks_parquet(path: Path, execution_key: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        df = pl.DataFrame(rows).with_columns(pl.lit(execution_key).alias("execution_key")).select(
            ["execution_key", "strategy", "date", "code", "name", "industry"]
        )
    else:
        df = pl.DataFrame(
            {
                "execution_key": [],
                "strategy": [],
                "date": [],
                "code": [],
                "name": [],
                "industry": [],
            }
        )
    df.write_parquet(path, compression="zstd")


def _write_signals_json(path: Path, payload: dict[str, dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _read_picks(execution_key: str) -> list[dict[str, Any]]:
    artifacts = storage.list_artifacts(execution_key, run_type="selection")
    match = next((item for item in artifacts if item["artifact_type"] == "picks_parquet"), None)
    if match is None:
        return []
    path = storage.artifact_path(match["storage_key"])
    if not path.exists():
        return []
    rows = pl.read_parquet(path).to_dicts()
    stock_meta = _load_stock_meta()
    return [_normalize_pick_row(row, stock_meta) for row in rows]


def _normalize_pick_row(row: dict[str, Any], stock_meta: dict[str, dict[str, str]]) -> dict[str, Any]:
    out = dict(row)
    if "execution_key" not in out and "run_id" in out:
        out["execution_key"] = out["run_id"]
    out.pop("run_id", None)
    code = str(out.get("code") or "").strip().zfill(6)
    meta = stock_meta.get(code, {})
    out["code"] = code
    out["name"] = str(out.get("name") or meta.get("name") or "")
    out["industry"] = str(out.get("industry") or meta.get("industry") or "")
    return out


def _load_stock_meta() -> dict[str, dict[str, str]]:
    if not STOCKLIST.exists():
        return {}
    try:
        with STOCKLIST.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            return {
                str(row.get("symbol") or "").strip().zfill(6): {
                    "name": str(row.get("name") or "").strip(),
                    "industry": str(row.get("industry") or "").strip(),
                }
                for row in reader
                if str(row.get("symbol") or "").strip()
            }
    except Exception:
        return {}


def _strategy_snapshots(strategy_names: list[str]) -> list[dict[str, Any]]:
    strategies = list_strategies().get("strategies", [])
    by_name = {item.get("name"): item for item in strategies if isinstance(item, dict)}
    return [
        by_name.get(name, {"name": name, "class": "", "description": "", "params": {}})
        for name in strategy_names
    ]


def _build_log_text(
    *,
    execution_key: str,
    date_text: str,
    strategy_names: list[str],
    summaries: list[dict[str, Any]],
    elapsed: float,
    signal_file: str,
) -> str:
    lines = [
        "选股运行日志",
        f"execution_key: {execution_key}",
        f"date: {date_text}",
        f"strategies: {', '.join(strategy_names)}",
        f"signal_file: {signal_file}",
        f"elapsed_seconds: {elapsed:.3f}",
        "",
        "summary:",
    ]
    for item in summaries:
        lines.append(
            f"- {item['strategy']}: count={item['count']}, "
            f"elapsed={float(item['elapsed_seconds']):.3f}s"
        )
    lines.append("")
    return "\n".join(lines)
