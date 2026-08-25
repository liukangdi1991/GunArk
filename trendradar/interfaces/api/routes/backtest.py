"""Backtest results routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.execution import (
    BacktestRequest as BacktestSubmitRequest,
    DeleteKeysRequest,
    SelectionBacktestRequest,
)

router = APIRouter(prefix="/api", tags=["backtest"])


def _executor(request: FastAPIRequest):
    return request.app.state.executor


def _market_store(request: FastAPIRequest):
    return request.app.state.market_store


def _signal_repo(request: FastAPIRequest):
    return request.app.state.signal_repo


def _artifacts_root(request: FastAPIRequest) -> Path:
    from trendradar.infrastructure.runtime import runtime_root
    return runtime_root() / "storage" / "objects" / "executions"


@router.get("/backtest-results")
def list_backtest_results(request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import list_backtest_results_payload
    return list_backtest_results_payload()


@router.get("/backtest-results/{execution_key}/report")
def get_backtest_report(execution_key: str, request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import backtest_result_payload

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    root = _artifacts_root(request)
    result_file = root / execution_key / "backtest" / "result.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    return backtest_result_payload(execution_key, include_report=True)


@router.delete("/backtest-results/{execution_key}")
def delete_backtest_result(execution_key: str, request: FastAPIRequest):
    # "." must not resolve to the executions root itself, and only directories
    # confirmed to hold a backtest may be deleted (see bulk_delete_backtests).
    if "/" in execution_key or "\\" in execution_key or ".." in execution_key or "." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    from trendradar.interfaces.api.presenters import bulk_delete_backtests

    result = bulk_delete_backtests([execution_key])
    if result["deleted"] == 0:
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    return result


@router.delete("/backtest-results")
def delete_backtest_results(
    body: DeleteKeysRequest | None = None,
    request: FastAPIRequest = None,
):
    """Delete only the requested backtests; a body-less request clears all."""
    from trendradar.interfaces.api.presenters import bulk_delete_backtests

    keys = body.execution_keys if body else None
    return bulk_delete_backtests(keys)


@router.post("/backtests")
def submit_backtest_route(body: BacktestSubmitRequest, request: FastAPIRequest):
    from datetime import date
    from trendradar.app.services.backtest_service import (
        submit_backtest,
        validate_backtest_prerequisites,
    )
    from trendradar.interfaces.api.presenters import (
        _now_iso,
        trading_dates_payload,
    )

    req = body.model_dump(exclude_none=True)
    execution_key = req.get("execution_key")
    signal_set = _signal_repo(request).load(execution_key) if execution_key else None
    if signal_set is None:
        raise HTTPException(status_code=404, detail=f"信号集不存在: {execution_key}")

    # Prerequisites: signal dates must leave enough trading days for buy + hold.
    # 超短线（entry_on_signal_day）只需持有期天数，T+1 需要多 1 天。
    dates = [date.fromisoformat(d) for d in trading_dates_payload()["dates"]]
    execution = req.get("execution") or {}
    reasons = validate_backtest_prerequisites(
        signal_set,
        dates,
        fixed_hold_n_days=execution.get("fixed_hold_n_days", 5),
        entry_on_signal_day=execution.get("entry_on_signal_day", False),
    )
    if reasons:
        raise HTTPException(status_code=400, detail="；".join(reasons))

    try:
        job_id = submit_backtest(
            _executor(request),
            _market_store(request),
            _signal_repo(request),
            req,
        )
        return {
            "execution_id": job_id,
            "execution_type": "backtest",
            "type": "backtest",
            "status": "submitted",
            "progress_current": 0,
            "progress_total": 0,
            "progress_message": "",
            "error_message": None,
            "result_url": None,
            "console_url": f"/console/{job_id}",
            "created_at": _now_iso(),
        }
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="提交失败，请查看执行控制台日志")


@router.post("/selection-backtest")
def submit_selection_backtest_route(body: SelectionBacktestRequest, request: FastAPIRequest):
    from trendradar.app.services.backtest_service import submit_selection_backtest

    try:
        job_id = submit_selection_backtest(
            _executor(request),
            _market_store(request),
            _signal_repo(request),
            body.model_dump(exclude_none=True),
        )
        return {"data": {"job_id": job_id, "status": "submitted"}}
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_selection_backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="提交失败，请查看执行控制台日志")
