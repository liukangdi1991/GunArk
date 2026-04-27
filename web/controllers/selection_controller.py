from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.schemas.selection import SelectionRequest
from web.services import selection_service


def create_selection(payload: SelectionRequest) -> dict[str, Any]:
    try:
        return selection_service.create_selection(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def list_selections(limit: int = 50) -> dict[str, Any]:
    return selection_service.list_selections(limit=limit)


def get_selection(run_id: str) -> dict[str, Any]:
    result = selection_service.get_selection(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return result
