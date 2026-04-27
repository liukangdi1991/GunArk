from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import selection_controller
from web.schemas.selection import SelectionRequest


router = APIRouter(prefix="/api/selections", tags=["selections"])


@router.post("")
def create_selection(payload: SelectionRequest) -> dict[str, Any]:
    return selection_controller.create_selection(payload)


@router.get("")
def list_selections(limit: int = 50) -> dict[str, Any]:
    return selection_controller.list_selections(limit=limit)


@router.get("/{run_id}")
def get_selection(run_id: str) -> dict[str, Any]:
    return selection_controller.get_selection(run_id)
