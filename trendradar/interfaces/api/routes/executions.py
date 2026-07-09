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


@router.get("/selection-results")
def list_selection_results(request: FastAPIRequest):
    from pathlib import Path
    from trendradar.infrastructure.runtime import runtime_root

    root = runtime_root() / "storage" / "objects" / "executions"
    items = []
    if root.exists():
        for exec_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            manifest = exec_dir / "selection" / "manifest.json"
            if not manifest.exists():
                continue
            try:
                import json
                data = json.loads(manifest.read_text(encoding="utf-8"))
                items.append({
                    "execution_key": exec_dir.name,
                    "execution_type": data.get("execution_type"),
                    "status": data.get("status"),
                    "created_at": data.get("created_at"),
                })
            except Exception:
                continue
    return {"data": {"items": items}}


@router.get("/selection-results/{execution_key}")
def get_selection_result(execution_key: str, request: FastAPIRequest):
    from pathlib import Path
    from trendradar.infrastructure.runtime import runtime_root

    signals_file = runtime_root() / "storage" / "objects" / "executions" / execution_key / "selection" / "signals.json" / "data.json"
    if not signals_file.exists():
        raise HTTPException(status_code=404, detail=f"Selection result not found for '{execution_key}'")

    import json
    return json.loads(signals_file.read_text(encoding="utf-8"))


@router.delete("/selection-results/{execution_key}")
def delete_selection_result(execution_key: str, request: FastAPIRequest):
    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    from pathlib import Path
    from trendradar.infrastructure.runtime import runtime_root
    import shutil

    root = runtime_root() / "storage" / "objects" / "executions"
    target = (root / execution_key).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Selection result not found for '{execution_key}'")
    shutil.rmtree(target)
    return {"data": {"execution_key": execution_key, "deleted": True}}
def cancel_execution(execution_id: str, request: FastAPIRequest):
    cancelled = _executor(request).cancel(execution_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found or already cancelled")
    return {"data": {"job_id": execution_id, "cancelled": True}}
