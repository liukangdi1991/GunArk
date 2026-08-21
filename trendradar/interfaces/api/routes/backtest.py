"""Backtest results routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.execution import (
    BacktestRequest as BacktestSubmitRequest,
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

    root = _artifacts_root(request)
    result_file = root / execution_key / "backtest" / "result.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    return backtest_result_payload(execution_key, include_report=True)


@router.delete("/backtest-results/{execution_key}")
def delete_backtest_result(execution_key: str, request: FastAPIRequest):
    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    root = _artifacts_root(request)
    target = (root / execution_key).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    import shutil
    shutil.rmtree(target)
    return {"data": {"execution_key": execution_key, "deleted": True}}


@router.delete("/backtest-results")
def delete_all_backtest_results(request: FastAPIRequest):
    root = _artifacts_root(request)
    count = 0
    if root.exists():
        import shutil
        for exec_dir in list(root.iterdir()):
            if exec_dir.is_dir() and (exec_dir / "backtest" / "metrics.json").exists():
                shutil.rmtree(exec_dir)
                count += 1
    return {"data": {"deleted": count}}


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

    # Prerequisites: signal dates must leave enough trading days for T+1 buy + hold.
    dates = [date.fromisoformat(d) for d in trading_dates_payload()["dates"]]
    reasons = validate_backtest_prerequisites(signal_set, dates)
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))
