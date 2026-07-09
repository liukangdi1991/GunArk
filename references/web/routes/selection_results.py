from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import selection_controller
from web.schemas.selection import DeleteSelectionResultsRequest


router = APIRouter(prefix="/api/selection-results", tags=["selection-results"])


@router.get("")
def list_selection_results(limit: int = 50) -> dict[str, Any]:
    return selection_controller.list_selections(limit=limit)


@router.delete("")
def delete_selection_results(payload: DeleteSelectionResultsRequest | None = None) -> dict[str, Any]:
    return selection_controller.delete_selections(payload)


@router.get("/{execution_key}")
def get_selection_result(execution_key: str) -> dict[str, Any]:
    return selection_controller.get_selection(execution_key)


@router.delete("/{execution_key}")
def delete_selection_result(execution_key: str) -> dict[str, Any]:
    return selection_controller.delete_selection(execution_key)
