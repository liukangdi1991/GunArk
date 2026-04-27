from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from backtest.service import run_backtest as run_backtest_job
from web.core.config import storage
from web.schemas.backtest import BacktestRequest


def create_backtest(payload: BacktestRequest) -> dict[str, Any]:
    result = run_backtest_job(
        start=_parse_date(payload.from_date),
        end=_parse_date(payload.to_date),
        strategies=payload.strategies,
        mode=payload.mode,
        cash_per_trade=payload.cash_per_trade,
        run_name=payload.run_name,
    )
    return {
        "run_id": result["run_id"],
        "run_dir": str(result["run_dir"]),
        "strategies": result["strategies"],
    }


def list_backtests(limit: int = 50) -> dict[str, Any]:
    return {"runs": storage.list_backtest_runs(limit=limit)}


def get_backtest(run_id: str) -> dict[str, Any] | None:
    run = storage.get_backtest_run(run_id)
    if run is None:
        return None
    return {"run": run, "artifacts": storage.list_artifacts(run_id)}


def get_backtest_report(run_id: str) -> dict[str, Any] | None:
    run = storage.get_backtest_run(run_id)
    if run is None:
        return None
    return {
        "run": run,
        "artifacts": storage.list_artifacts(run_id),
        "trades": _read_artifact_records(run_id, "trades_parquet"),
        "equity": _read_artifact_records(run_id, "equity_parquet"),
        "skips": _read_artifact_records(run_id, "skips_parquet"),
    }


def _parse_date(value: str) -> date:
    value = str(value).strip()
    if "-" in value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.strptime(value, "%Y%m%d").date()


def _read_artifact_records(run_id: str, artifact_type: str) -> list[dict[str, Any]]:
    artifacts = storage.list_artifacts(run_id)
    match = next((item for item in artifacts if item["artifact_type"] == artifact_type), None)
    if match is None:
        return []
    path = storage.artifact_path(match["storage_key"])
    if not path.exists():
        return []
    return [_jsonable_record(row) for row in pl.read_parquet(path).to_dicts()]


def _jsonable_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in row.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
