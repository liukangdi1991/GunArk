from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


ExecutionType = Literal[
    "selection_latest",
    "selection_single",
    "selection_batch",
    "backtest",
    "backtest_from_selection",
    "selection_backtest",
    "market_data_sync",
]


class ExecutionRequest(BaseModel):
    type: ExecutionType
    params: dict[str, Any] = {}
