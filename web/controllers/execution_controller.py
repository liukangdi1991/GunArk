from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.schemas.backtest import BacktestRequest
from web.schemas.execution import ExecutionRequest
from web.schemas.market import FetchMarketRequest
from web.schemas.selection import BatchSelectionRequest, SelectionRequest
from web.services import execution_service


def create_execution(payload: ExecutionRequest) -> dict[str, Any]:
    try:
        return execution_service.submit_execution(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_selection_execution(payload: SelectionRequest) -> dict[str, Any]:
    return execution_service.submit_selection(payload)


def create_batch_selection_execution(payload: BatchSelectionRequest) -> dict[str, Any]:
    return execution_service.submit_batch_selection(payload)


def create_backtest_execution(payload: BacktestRequest) -> dict[str, Any]:
    return execution_service.submit_backtest(payload)


def create_market_fetch_execution(payload: FetchMarketRequest) -> dict[str, Any]:
    return execution_service.submit_market_fetch(payload)


def get_execution(execution_id: str) -> dict[str, Any]:
    execution = execution_service.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return execution


def read_console(execution_id: str, offset: int = 0) -> dict[str, Any]:
    console = execution_service.read_console(execution_id, offset=offset)
    if console is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return console
