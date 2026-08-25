"""Execution job submission and status routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.execution import (
    ExecutionRequest,
    SelectionBacktestRequest,
    BacktestRequest,
    JobStatusResponse,
    DeleteKeysRequest,
)

router = APIRouter(prefix="/api", tags=["executions"])


def _executor(request: FastAPIRequest):
    return request.app.state.executor


def _market_store(request: FastAPIRequest):
    return request.app.state.market_store


def _store(request: FastAPIRequest):
    return request.app.state.store


@router.post("/executions", response_model=dict)
def submit_execution(body: ExecutionRequest, request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import submit_execution_payload

    try:
        return submit_execution_payload(
            _executor(request),
            _market_store(request),
            _store(request),
            body.model_dump(exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="提交失败，请查看执行控制台日志")


@router.get("/executions/{execution_id}", response_model=JobStatusResponse)
def get_execution_status(execution_id: str, request: FastAPIRequest):
    state = _executor(request).get_state(execution_id)
    if state.get("status") == "unknown":
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
    return state


@router.get("/executions/{execution_id}/console")
def get_execution_console(execution_id: str, request: FastAPIRequest, offset: int = Query(default=0, ge=0)):
    from trendradar.interfaces.api.presenters import console_payload

    try:
        return console_payload(_executor(request), execution_id, offset=offset)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")


@router.get("/selection-results")
def list_selection_results(request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import list_selection_results_payload
    return list_selection_results_payload()


@router.get("/selection-results/{execution_key}")
def get_selection_result(execution_key: str, request: FastAPIRequest):
    from pathlib import Path
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.interfaces.api.presenters import selection_result_payload

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    signals_file = (
        Path(runtime_root())
        / "storage" / "objects" / "executions" / execution_key / "selection" / "signals.json"
    )
    if not signals_file.exists():
        raise HTTPException(status_code=404, detail=f"Selection result not found for '{execution_key}'")
    return selection_result_payload(execution_key, include_detail=True)


@router.delete("/selection-results")
def delete_selection_results(body: DeleteKeysRequest | None = None, request: FastAPIRequest = None):
    from trendradar.interfaces.api.presenters import bulk_delete_selections

    keys = body.execution_keys if body else None
    return bulk_delete_selections(keys)


@router.delete("/selection-results/{execution_key}")
def delete_selection_result(execution_key: str, request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import bulk_delete_selections

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    from pathlib import Path
    from trendradar.infrastructure.runtime import runtime_root

    root = Path(runtime_root()) / "storage" / "objects" / "executions"
    if not (root / execution_key).exists():
        raise HTTPException(status_code=404, detail=f"Selection result not found for '{execution_key}'")
    return bulk_delete_selections([execution_key])


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, request: FastAPIRequest):
    cancelled = _executor(request).cancel(execution_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found or already cancelled")
    return {"data": {"job_id": execution_id, "cancelled": True}}
