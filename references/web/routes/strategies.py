from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from web.controllers import strategy_controller


router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def list_strategies() -> dict[str, Any]:
    return strategy_controller.list_strategies()
