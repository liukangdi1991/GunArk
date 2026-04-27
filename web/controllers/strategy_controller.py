from __future__ import annotations

from typing import Any

from web.services import strategy_service


def list_strategies() -> dict[str, Any]:
    return strategy_service.list_strategies()
