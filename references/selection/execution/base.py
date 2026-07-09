from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

import polars as pl


class StrategySelectionRunner(ABC):
    """策略运行模板：统一执行“预筛 -> 精筛”流程。"""

    def __init__(self, selector: Any) -> None:
        self.selector = selector

    def run_selection(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        """
        模板方法：
        1) run_prefilter：先做快速预筛，缩小候选范围
        2) run_final_filter：再执行策略核心指标判断

        这样做可以保证：
        - 逻辑行为与旧版一致
        - 大部分耗时在向量化预筛阶段被消化
        """
        candidates = self.run_prefilter(date_obj=date_obj, data_table=data_table)
        return self.run_final_filter(
            date_obj=date_obj,
            data_table=data_table,
            candidates=candidates,
            get_data_dict=get_data_dict,
        )

    @abstractmethod
    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        """执行预筛，返回候选 code 列表；返回 None 表示不做预筛。"""

    @abstractmethod
    def run_final_filter(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        candidates: Optional[List[str]],
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        """在预筛结果上执行最终策略指标判断。"""
