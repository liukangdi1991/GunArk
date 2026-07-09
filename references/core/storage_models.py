from __future__ import annotations

from enum import StrEnum
from typing import Any


class ExecutionType(StrEnum):
    SELECTION = "selection"
    BACKTEST = "backtest"
    SELECTION_BACKTEST = "selection_backtest"


class ExecutionItemType(StrEnum):
    SELECTION_STRATEGY = "selection_strategy"
    TRADE_STRATEGY = "trade_strategy"
    CAPITAL_MODEL = "capital_model"
    EXECUTION_CONFIG = "execution_config"


class SelectionResultInUseError(ValueError):
    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        self.blockers = blockers
        details = []
        for item in blockers[:5]:
            selection_key = str(item.get("selection_execution_key") or "")
            backtest_keys = [str(key) for key in item.get("backtest_execution_keys") or []]
            suffix = ", ".join(backtest_keys[:3])
            if len(backtest_keys) > 3:
                suffix = f"{suffix} 等 {len(backtest_keys)} 个回测"
            details.append(f"{selection_key} 被回测 {suffix} 引用")
        message = "选股历史已被回测结果引用，不能删除。请先删除相关回测数据后，再删除选股历史。"
        if details:
            message = f"{message} {'；'.join(details)}"
        super().__init__(message)
