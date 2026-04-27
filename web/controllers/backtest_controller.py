from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.schemas.backtest import BacktestRequest
from web.services import backtest_service


def create_backtest(payload: BacktestRequest) -> dict[str, Any]:
    try:
        return backtest_service.create_backtest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def list_backtests(limit: int = 50) -> dict[str, Any]:
    return backtest_service.list_backtests(limit=limit)


def get_backtest(run_id: str) -> dict[str, Any]:
    result = backtest_service.get_backtest(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return result


def get_backtest_report(run_id: str) -> dict[str, Any]:
    result = backtest_service.get_backtest_report(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return result
