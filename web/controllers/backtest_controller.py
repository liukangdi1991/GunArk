from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.schemas.backtest import BacktestRequest, DeleteBacktestResultsRequest
from web.services import backtest_service


def create_backtest(payload: BacktestRequest) -> dict[str, Any]:
    try:
        return backtest_service.create_backtest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def list_backtests(limit: int = 50) -> dict[str, Any]:
    return backtest_service.list_backtests(limit=limit)


def get_backtest(execution_key: str) -> dict[str, Any]:
    result = backtest_service.get_backtest(execution_key)
    if result is None:
        raise HTTPException(status_code=404, detail="execution 不存在")
    return result


def get_backtest_report(execution_key: str) -> dict[str, Any]:
    result = backtest_service.get_backtest_report(execution_key)
    if result is None:
        raise HTTPException(status_code=404, detail="execution 不存在")
    return result


def delete_backtest(execution_key: str) -> dict[str, Any]:
    result = backtest_service.delete_backtest(execution_key)
    if result["deleted"] == 0:
        raise HTTPException(status_code=404, detail="execution 不存在")
    return result


def delete_backtests(payload: DeleteBacktestResultsRequest | None = None) -> dict[str, Any]:
    execution_keys = payload.execution_keys if payload else None
    return backtest_service.delete_backtests(execution_keys=execution_keys)
