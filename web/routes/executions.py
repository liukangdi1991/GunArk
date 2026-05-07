from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import execution_controller
from web.schemas.backtest import BacktestRequest
from web.schemas.execution import ExecutionRequest
from web.schemas.market import FetchMarketRequest
from web.schemas.selection import BatchSelectionRequest, SelectionRequest


router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.post("")
def create_execution(payload: ExecutionRequest) -> dict[str, Any]:
    return execution_controller.create_execution(payload)


@router.post("/selections")
def create_selection_execution(payload: SelectionRequest) -> dict[str, Any]:
    return execution_controller.create_selection_execution(payload)


@router.post("/selections/batch")
def create_batch_selection_execution(payload: BatchSelectionRequest) -> dict[str, Any]:
    return execution_controller.create_batch_selection_execution(payload)


@router.post("/backtests")
def create_backtest_execution(payload: BacktestRequest) -> dict[str, Any]:
    return execution_controller.create_backtest_execution(payload)


@router.post("/market/fetch")
def create_market_fetch_execution(payload: FetchMarketRequest) -> dict[str, Any]:
    return execution_controller.create_market_fetch_execution(payload)


@router.get("/{execution_id}/console")
def read_console(execution_id: str, offset: int = 0) -> dict[str, Any]:
    return execution_controller.read_console(execution_id, offset=offset)


@router.post("/{execution_id}/cancel")
def cancel_execution(execution_id: str) -> dict[str, Any]:
    return execution_controller.cancel_execution(execution_id)


@router.get("/{execution_id}")
def get_execution(execution_id: str) -> dict[str, Any]:
    return execution_controller.get_execution(execution_id)
