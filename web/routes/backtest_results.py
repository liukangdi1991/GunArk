from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import backtest_controller
from web.schemas.backtest import DeleteBacktestResultsRequest


router = APIRouter(prefix="/api/backtest-results", tags=["backtest-results"])


@router.get("")
def list_backtest_results(limit: int = 50) -> dict[str, Any]:
    return backtest_controller.list_backtests(limit=limit)


@router.delete("")
def delete_backtest_results(payload: DeleteBacktestResultsRequest | None = None) -> dict[str, Any]:
    return backtest_controller.delete_backtests(payload)


@router.get("/{execution_key}/report")
def get_backtest_result_report(execution_key: str) -> dict[str, Any]:
    return backtest_controller.get_backtest_report(execution_key)


@router.get("/{execution_key}/trades")
def get_backtest_result_trades(execution_key: str) -> dict[str, Any]:
    report = backtest_controller.get_backtest_report(execution_key)
    return {"trades": report["trades"]}


@router.get("/{execution_key}/equity")
def get_backtest_result_equity(execution_key: str) -> dict[str, Any]:
    report = backtest_controller.get_backtest_report(execution_key)
    return {"equity": report["equity"]}


@router.get("/{execution_key}/skips")
def get_backtest_result_skips(execution_key: str) -> dict[str, Any]:
    report = backtest_controller.get_backtest_report(execution_key)
    return {"skips": report["skips"]}


@router.get("/{execution_key}")
def get_backtest_result(execution_key: str) -> dict[str, Any]:
    return backtest_controller.get_backtest(execution_key)


@router.delete("/{execution_key}")
def delete_backtest_result(execution_key: str) -> dict[str, Any]:
    return backtest_controller.delete_backtest(execution_key)
