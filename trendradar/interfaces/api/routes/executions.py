"""Execution job submission and status routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.execution import (
    ExecutionRequest,
    BatchExecutionRequest,
    SelectionBacktestRequest,
    BacktestRequest,
    JobStatusResponse,
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
    from trendradar.app.services.selection_service import submit_selection

    try:
        req_dict = body.model_dump(exclude_none=True)
        job_id = submit_selection(
            _executor(request),
            _market_store(request),
            req_dict,
            _store(request),
        )
        return {"data": {"job_id": job_id, "status": "submitted"}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/executions/{execution_id}", response_model=JobStatusResponse)
def get_execution_status(execution_id: str, request: FastAPIRequest):
    state = _executor(request).get_state(execution_id)
    if state.get("status") == "unknown":
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
    return state


@router.get("/executions/{execution_id}/console")
def get_execution_console(execution_id: str, request: FastAPIRequest, offset: int = Query(default=0, ge=0)):
    console = _executor(request).get_console(execution_id, offset=offset)
    return {"data": {"job_id": execution_id, "console": console, "offset": offset}}


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, request: FastAPIRequest):
    cancelled = _executor(request).cancel(execution_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found or already cancelled")
    return {"data": {"job_id": execution_id, "cancelled": True}}
