"""Backtest results routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.backtest import (
    BacktestSubmitRequest,
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
    root = _artifacts_root(request)
    items = []
    if root.exists():
        for exec_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not exec_dir.is_dir():
                continue
            backtest_dir = exec_dir / "backtest"
            metrics_file = backtest_dir / "metrics.json"
            if not metrics_file.exists():
                continue
            try:
                import json
                metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
                items.append({
                    "execution_key": exec_dir.name,
                    "job_id": exec_dir.name,
                    "summary": metrics,
                })
            except Exception:
                continue
    return {"data": {"items": items}}


@router.get("/backtest-results/{execution_key}/report")
def get_backtest_report(execution_key: str, request: FastAPIRequest):
    root = _artifacts_root(request)
    result_file = root / execution_key / "backtest" / "result.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    import json
    return json.loads(result_file.read_text(encoding="utf-8"))


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
            if exec_dir.is_dir() and (exec_dir / "backtest" / "result.json").exists():
                shutil.rmtree(exec_dir)
                count += 1
    return {"data": {"deleted": count}}


@router.post("/backtests")
def submit_backtest_route(body: BacktestSubmitRequest, request: FastAPIRequest):
    from trendradar.app.services.backtest_service import submit_backtest

    try:
        job_id = submit_backtest(
            _executor(request),
            _market_store(request),
            _signal_repo(request),
            body.model_dump(exclude_none=True),
        )
        return {"data": {"job_id": job_id, "status": "submitted"}}
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
