from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import backtest_controller
from web.schemas.backtest import BacktestRequest


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


@router.post("")
def create_backtest(payload: BacktestRequest) -> dict[str, Any]:
    return backtest_controller.create_backtest(payload)


@router.get("")
def list_backtests(limit: int = 50) -> dict[str, Any]:
    return backtest_controller.list_backtests(limit=limit)


@router.get("/{run_id}")
def get_backtest(run_id: str) -> dict[str, Any]:
    return backtest_controller.get_backtest(run_id)


@router.get("/{run_id}/report")
def get_backtest_report(run_id: str) -> dict[str, Any]:
    return backtest_controller.get_backtest_report(run_id)
