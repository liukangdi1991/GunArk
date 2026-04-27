from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from select_stock import (
    build_strategy_runner,
    get_stock_names,
    load_data_table,
    load_default_strategy_aliases,
    load_strategies_from_config,
    save_results,
    table_to_data_dict,
)
from web.core.config import ROOT, storage
from web.schemas.selection import SelectionRequest
from web.services.strategy_service import list_strategies


DATA_DIR = ROOT / "db"
CONFIG_PATH = ROOT / "configs.json"
SIGNAL_DIR = ROOT / "results" / "signals"


def create_selection(payload: SelectionRequest) -> dict[str, Any]:
    date_obj = _parse_date(payload.date) if payload.date else _get_latest_trade_date()
    if date_obj is None:
        raise ValueError("未找到可用行情交易日")

    data_table = load_data_table(str(DATA_DIR), payload.tickers)
    if data_table.is_empty():
        raise ValueError("无行情数据，请先拉取行情")

    strategies = load_strategies_from_config(CONFIG_PATH)
    strategy_names = _resolve_strategy_names(payload.strategies, strategies)
    if not strategy_names:
        raise ValueError("没有可运行的选股策略")

    data_dict_cache = None

    def get_data_dict():
        nonlocal data_dict_cache
        if data_dict_cache is None:
            data_dict_cache = table_to_data_dict(data_table)
        return data_dict_cache

    stock_names = get_stock_names()
    all_results: dict[str, dict[str, Any]] = {}
    pick_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    total_start = time.time()
    date_text = date_obj.isoformat()

    for strategy_name in strategy_names:
        selector = strategies[strategy_name]["selector"]
        runner = build_strategy_runner(selector)
        start = time.time()
        picks = runner.run_selection(
            date_obj=date_obj,
            data_table=data_table,
            get_data_dict=get_data_dict,
        )
        elapsed = time.time() - start

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
                    "name": stock_names.get(normalized_code, ""),
                }
            )

    signal_file = save_results(all_results, date_text, str(SIGNAL_DIR))
    run_id = _build_run_id(date_obj, strategy_names)
    object_dir = storage.objects_root / "selections" / run_id
    object_dir.mkdir(parents=True, exist_ok=True)

    picks_path = object_dir / "picks.parquet"
    log_path = object_dir / "log.txt"
    _write_picks_parquet(picks_path, run_id, pick_rows)
    log_path.write_text(
        _build_log_text(
            run_id=run_id,
            date_text=date_text,
            strategy_names=strategy_names,
            summaries=summaries,
            elapsed=time.time() - total_start,
            signal_file=signal_file,
        ),
        encoding="utf-8",
    )

    snapshots = _strategy_snapshots(strategy_names)
    storage.record_selection_run(
        run_id=run_id,
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
        run_id=run_id,
        artifact_type="picks_parquet",
        path=picks_path,
        mime_type="application/vnd.apache.parquet",
    )
    storage.register_artifact(
        run_type="selection",
        run_id=run_id,
        artifact_type="log_txt",
        path=log_path,
        mime_type="text/plain; charset=utf-8",
    )

    return {
        "run_id": run_id,
        "selection_date": date_text,
        "strategies": strategy_names,
        "summary": summaries,
        "signal_file": signal_file,
    }


def list_selections(limit: int = 50) -> dict[str, Any]:
    return {"runs": storage.list_selection_runs(limit=limit)}


def get_selection(run_id: str) -> dict[str, Any] | None:
    run = storage.get_selection_run(run_id)
    if run is None:
        return None
    return {
        "run": run,
        "artifacts": storage.list_artifacts(run_id, run_type="selection"),
        "picks": _read_picks(run_id),
    }


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


def _build_run_id(date_obj: date, strategy_names: list[str]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(strategy_names) == 1:
        suffix = _sanitize(strategy_names[0])
    else:
        suffix = f"{len(strategy_names)}strategies"
    date_part = date_obj.strftime("%Y%m%d")
    return f"{ts}_{date_part}_{suffix}" if suffix else f"{ts}_{date_part}"


def _sanitize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("_")


def _write_picks_parquet(path: Path, run_id: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        df = pl.DataFrame(rows).with_columns(pl.lit(run_id).alias("run_id")).select(
            ["run_id", "strategy", "date", "code", "name"]
        )
    else:
        df = pl.DataFrame(
            {
                "run_id": [],
                "strategy": [],
                "date": [],
                "code": [],
                "name": [],
            }
        )
    df.write_parquet(path, compression="zstd")


def _read_picks(run_id: str) -> list[dict[str, Any]]:
    artifacts = storage.list_artifacts(run_id, run_type="selection")
    match = next((item for item in artifacts if item["artifact_type"] == "picks_parquet"), None)
    if match is None:
        return []
    path = storage.artifact_path(match["storage_key"])
    if not path.exists():
        return []
    return pl.read_parquet(path).to_dicts()


def _strategy_snapshots(strategy_names: list[str]) -> list[dict[str, Any]]:
    strategies = list_strategies().get("strategies", [])
    by_name = {item.get("name"): item for item in strategies if isinstance(item, dict)}
    return [
        by_name.get(name, {"name": name, "class": "", "description": "", "params": {}})
        for name in strategy_names
    ]


def _build_log_text(
    *,
    run_id: str,
    date_text: str,
    strategy_names: list[str],
    summaries: list[dict[str, Any]],
    elapsed: float,
    signal_file: str,
) -> str:
    lines = [
        "选股运行日志",
        f"run_id: {run_id}",
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
