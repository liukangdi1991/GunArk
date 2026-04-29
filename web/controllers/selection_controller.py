from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.schemas.selection import BatchSelectionRequest, DeleteSelectionResultsRequest, SelectionRequest
from web.services import selection_service


def create_selection(payload: SelectionRequest) -> dict[str, Any]:
    try:
        return selection_service.create_selection(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_batch_selection(payload: BatchSelectionRequest) -> dict[str, Any]:
    try:
        return selection_service.create_batch_selection(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def list_selections(limit: int = 50) -> dict[str, Any]:
    return selection_service.list_selections(limit=limit)


def get_selection(execution_key: str) -> dict[str, Any]:
    result = selection_service.get_selection(execution_key)
    if result is None:
        raise HTTPException(status_code=404, detail="execution 不存在")
    return result


def delete_selection(execution_key: str) -> dict[str, Any]:
    result = selection_service.delete_selection(execution_key)
    if result["deleted"] == 0:
        raise HTTPException(status_code=404, detail="execution 不存在")
    return result


def delete_selections(payload: DeleteSelectionResultsRequest | None = None) -> dict[str, Any]:
    execution_keys = payload.execution_keys if payload else None
    return selection_service.delete_selections(execution_keys=execution_keys)
