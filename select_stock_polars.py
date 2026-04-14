"""
选股程序 - Polars 版本
- 使用 Polars 直接读取 CSV（不转换）
- 保持与原版完全一致的选股逻辑
- 美观的 Rich 输出界面
"""

import argparse
from abc import ABC, abstractmethod
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import polars as pl
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from Selector_polars import (
    BBIKDJSelector,
    SuperB1Selector,
    PeakKDJSelector,
    BBIShortLongSelector,
    MA60CrossVolumeWaveSelector,
    BigBullishVolumeSelector,
    compute_kdj,
    compute_bbi,
    bbi_deriv_uptrend,
)

console = Console()

# ─────────────────────────── 配置 ─────────────────────────── #

# 策略类名到 emoji 的映射
STRATEGY_EMOJIS = {
    "BBIKDJSelector": "🔥",
    "SuperB1Selector": "⚡",
    "PeakKDJSelector": "🎫",
    "BBIShortLongSelector": "🕳️",
    "MA60CrossVolumeWaveSelector": "📈",
    "BigBullishVolumeSelector": "💪",
}

# 类名到类的映射
SELECTOR_CLASSES = {
    "BBIKDJSelector": BBIKDJSelector,
    "SuperB1Selector": SuperB1Selector,
    "PeakKDJSelector": PeakKDJSelector,
    "BBIShortLongSelector": BBIShortLongSelector,
    "MA60CrossVolumeWaveSelector": MA60CrossVolumeWaveSelector,
    "BigBullishVolumeSelector": BigBullishVolumeSelector,
}


def load_config(cfg_path: Path) -> List[Dict[str, Any]]:
    """从 configs.json 加载策略配置"""
    if not cfg_path.exists():
        console.print(f"[red]❌ 配置文件 {cfg_path} 不存在[/red]")
        sys.exit(1)
    with cfg_path.open(encoding="utf-8") as f:
        cfg_raw = json.load(f)

    # 兼容三种结构：单对象、对象数组、或带 selectors 键
    if isinstance(cfg_raw, list):
        cfgs = cfg_raw
    elif isinstance(cfg_raw, dict) and "selectors" in cfg_raw:
        cfgs = cfg_raw["selectors"]
    else:
        cfgs = [cfg_raw]

    if not cfgs:
        console.print("[red]❌ configs.json 未定义任何 Selector[/red]")
        sys.exit(1)

    return cfgs


def instantiate_selector(cfg: Dict[str, Any]):
    """动态加载 Selector 类并实例化"""
    cls_name: str = cfg.get("class")
    if not cls_name:
        raise ValueError("缺少 class 字段")

    cls = SELECTOR_CLASSES.get(cls_name)
    if cls is None:
        raise ImportError(f"未知的 Selector 类: {cls_name}")

    params = cfg.get("params", {})
    alias = cfg.get("alias", cls_name)
    emoji = STRATEGY_EMOJIS.get(cls_name, "📊")
    return alias, cls(**params), emoji


def load_strategies_from_config(cfg_path: Path) -> Dict[str, Dict[str, Any]]:
    """从配置文件加载策略，返回与 STRATEGIES 相同格式的字典"""
    cfgs = load_config(cfg_path)
    strategies = {}
    for cfg in cfgs:
        if cfg.get("activate", True) is False:
            continue
        try:
            alias, selector, emoji = instantiate_selector(cfg)
            strategies[alias] = {
                "selector": selector,
                "emoji": emoji,
            }
        except Exception as e:
            console.print(f"[yellow]⚠️  跳过配置 {cfg}: {e}[/yellow]")
    return strategies


# ─────────────────────────── 数据加载 ─────────────────────────── #

def load_data_polars_table(data_dir: str, tickers: Optional[List[str]] = None) -> pl.DataFrame:
    """读取并返回按 code/date 排序的大表"""
    data_path = Path(data_dir)
    
    # 获取 parquet 文件列表
    if tickers:
        files = [str(data_path / f"{t}.parquet") for t in tickers if (data_path / f"{t}.parquet").exists()]
    else:
        files = [str(f) for f in data_path.glob("*.parquet")]
    
    if not files:
        return pl.DataFrame()
    
    # 批量读取，包含文件路径
    df_all = pl.read_parquet(files, include_file_paths="file_path")
    
    # 提取股票代码
    return df_all.with_columns(
        pl.col("file_path").str.extract(r"([^/]+)\.parquet$").alias("code")
    ).drop("file_path").sort(["code", "date"])


def table_to_data_dict(df_all: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    """将大表转换为旧版 Dict[code, DataFrame] 结构"""
    data = {}
    for code, df in df_all.partition_by("code", as_dict=True).items():
        data[code[0]] = df.drop("code")
    return data


def load_data_polars(data_dir: str, tickers: Optional[List[str]] = None) -> Dict[str, pl.DataFrame]:
    """使用 Polars 批量读取 Parquet 文件（旧接口）"""
    return table_to_data_dict(load_data_polars_table(data_dir, tickers))


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


class DefaultSelectionRunner(StrategySelectionRunner):
    """通用 Runner：不做预筛，直接沿用策略原生 select。"""

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        return None

    def run_final_filter(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        candidates: Optional[List[str]],
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        data = get_data_dict()
        if candidates is None:
            return self.selector.select(date_obj, data)
        subset = {code: data[code] for code in candidates if code in data}
        if not subset:
            return []
        return self.selector.select(date_obj, subset)


class BBIKDJSelectionRunner(StrategySelectionRunner):
    """BBIKDJ 专用 Runner：大表预筛 + 候选精筛。"""

    selector: BBIKDJSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self.selector.max_window + 20
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        prev_close = close.shift(1).over("code")

        # 预筛阶段只保留“可向量化且代价低”的条件
        filtered_codes = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                prev_close.alias("prev_close"),
                close.rolling_max(window_size=self.selector.max_window, min_samples=1).over("code").alias("close_roll_max"),
                close.rolling_min(window_size=self.selector.max_window, min_samples=1).over("code").alias("close_roll_min"),
                close.rolling_mean(window_size=60, min_samples=1).over("code").alias("MA60"),
                (
                    close.ewm_mean(span=12, adjust=False).over("code")
                    - close.ewm_mean(span=26, adjust=False).over("code")
                ).alias("DIF"),
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("ZXDQ"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("ZXDKX"),
                (
                    (
                        close.shift(1).over("code")
                        < close.rolling_mean(window_size=60, min_samples=1).over("code").shift(1).over("code")
                    )
                    & (close >= close.rolling_mean(window_size=60, min_samples=1).over("code"))
                ).cast(pl.Int8).alias("cross_up"),
            ])
            .with_columns([
                pl.col("cross_up")
                .rolling_max(window_size=self.selector.max_window, min_samples=1)
                .over("code")
                .alias("has_cross_up"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("prev_close") > 0)
                & (low > 0)
                & ((close / pl.col("prev_close") - 1.0).abs() < 0.02)
                & (((high - low) / low) < 0.07)
                & (pl.col("close_roll_min") > 0)
                & ((pl.col("close_roll_max") / pl.col("close_roll_min") - 1.0) <= self.selector.price_range_pct)
                & (close >= pl.col("MA60"))
                & (pl.col("has_cross_up") > 0)
                & (pl.col("DIF") > 0)
                & (pl.col("ZXDKX").is_not_null())
                & (close > pl.col("ZXDKX"))
                & (pl.col("ZXDQ") > pl.col("ZXDKX"))
            )
            .select("code")
            .collect()
        )
        return filtered_codes["code"].to_list() if not filtered_codes.is_empty() else []

    def run_final_filter(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        candidates: Optional[List[str]],
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        if not candidates:
            return []

        need_len = self.selector.max_window + 20
        candidates_hist = (
            data_table.filter((pl.col("date") <= date_obj) & (pl.col("code").is_in(candidates)))
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
        )

        picks: List[str] = []
        for code in candidates:
            hist = candidates_hist.filter(pl.col("code") == code).drop("code")
            if hist.is_empty():
                continue

            # 精筛阶段保留复杂指标，确保策略行为与旧版一致
            if not bbi_deriv_uptrend(
                compute_bbi(hist),
                min_window=self.selector.bbi_min_window,
                max_window=self.selector.max_window,
                q_threshold=self.selector.bbi_q_threshold,
            ):
                continue

            j_series = compute_kdj(hist)["J"]
            j_today = float(j_series[-1])
            j_window = j_series.tail(self.selector.max_window).drop_nulls()
            if len(j_window) == 0:
                continue
            j_quantile = float(j_window.quantile(self.selector.j_q_threshold, interpolation="linear"))
            if not (j_today < self.selector.j_threshold or j_today <= j_quantile):
                continue

            picks.append(code)
        return picks


class SuperB1SelectionRunner(DefaultSelectionRunner):
    """SuperB1 Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: SuperB1Selector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = self.selector.lookback_n + self.selector._extra_for_bbi
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        prev_close = close.shift(1).over("code")

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("prev_close") > 0)
                & (low > 0)
                # 统一当日过滤
                & ((close / pl.col("prev_close") - 1.0).abs() < 0.02)
                & (((high - low) / low) < 0.07)
                # SuperB1 的末日跌幅约束
                & (((pl.col("prev_close") - close) / pl.col("prev_close")) >= self.selector.price_drop_pct)
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class PeakKDJSelectionRunner(DefaultSelectionRunner):
    """PeakKDJ Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: PeakKDJSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = self.selector.max_window + 20
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        prev_close = close.shift(1).over("code")

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                prev_close.alias("prev_close"),
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("ZXDQ"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("ZXDKX"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("prev_close") > 0)
                & (low > 0)
                # 统一当日过滤
                & ((close / pl.col("prev_close") - 1.0).abs() < 0.02)
                & (((high - low) / low) < 0.07)
                # 知行末日条件
                & (pl.col("ZXDKX").is_not_null())
                & (close > pl.col("ZXDKX"))
                & (pl.col("ZXDQ") > pl.col("ZXDKX"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class BBIShortLongSelectionRunner(DefaultSelectionRunner):
    """BBIShortLong Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: BBIShortLongSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = max(
            max(self.selector.n_short, self.selector.n_long) + self.selector.bbi_min_window + self.selector.m,
            self.selector.max_window,
        )
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        prev_close = close.shift(1).over("code")
        low_short = low.rolling_min(window_size=self.selector.n_short, min_samples=1).over("code")
        high_close_short = close.rolling_max(window_size=self.selector.n_short, min_samples=1).over("code")
        low_long = low.rolling_min(window_size=self.selector.n_long, min_samples=1).over("code")
        high_close_long = close.rolling_max(window_size=self.selector.n_long, min_samples=1).over("code")

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
                (
                    (close - low_short)
                    / (high_close_short - low_short + 1e-9)
                    * 100.0
                ).alias("RSV_short"),
                (
                    (close - low_long)
                    / (high_close_long - low_long + 1e-9)
                    * 100.0
                ).alias("RSV_long"),
                (
                    close.ewm_mean(span=12, adjust=False).over("code")
                    - close.ewm_mean(span=26, adjust=False).over("code")
                ).alias("DIF"),
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("ZXDQ"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("ZXDKX"),
            ])
            .with_columns([
                (
                    pl.col("RSV_long")
                    .ge(self.selector.upper_rsv_threshold)
                    .cast(pl.Int8)
                    .rolling_min(window_size=self.selector.m, min_samples=self.selector.m)
                    .over("code")
                ).alias("long_ok_roll"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("prev_close") > 0)
                & (low > 0)
                # 统一当日过滤
                & ((close / pl.col("prev_close") - 1.0).abs() < 0.02)
                & (((high - low) / low) < 0.07)
                # 可快速判断的必要条件
                & (pl.col("long_ok_roll") == 1)
                & (pl.col("RSV_short") >= self.selector.upper_rsv_threshold)
                & (pl.col("DIF") > 0)
                & (pl.col("ZXDKX").is_not_null())
                & (close > pl.col("ZXDKX"))
                & (pl.col("ZXDQ") > pl.col("ZXDKX"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class MA60CrossVolumeWaveSelectionRunner(DefaultSelectionRunner):
    """MA60CrossVolumeWave Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: MA60CrossVolumeWaveSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = max(
            60 + self.selector.lookback_n + self.selector.ma60_slope_days,
            self.selector.max_window + 20,
        )
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        prev_close = close.shift(1).over("code")
        ma60 = close.rolling_mean(window_size=60, min_samples=1).over("code")
        cross_up = (
            (close.shift(1).over("code") < ma60.shift(1).over("code")) & (close >= ma60)
        ).cast(pl.Int8)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
                ma60.alias("MA60"),
                cross_up.alias("cross_up"),
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("ZXDQ"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("ZXDKX"),
            ])
            .with_columns([
                pl.col("cross_up")
                .rolling_max(window_size=self.selector.lookback_n, min_samples=1)
                .over("code")
                .alias("has_cross_up"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("prev_close") > 0)
                & (low > 0)
                # 统一当日过滤
                & ((close / pl.col("prev_close") - 1.0).abs() < 0.02)
                & (((high - low) / low) < 0.07)
                # 可快速判断的必要条件
                & (close >= pl.col("MA60"))
                & (pl.col("has_cross_up") > 0)
                & (pl.col("ZXDKX").is_not_null())
                & (close > pl.col("ZXDKX"))
                & (pl.col("ZXDQ") > pl.col("ZXDKX"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class BigBullishVolumeSelectionRunner(DefaultSelectionRunner):
    """BigBullishVolume Runner：Polars 预筛 + 原逻辑精筛。"""

    selector: BigBullishVolumeSelector

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []
        need_len = max(self.selector.min_history, self.selector.vol_lookback_n + 2)
        open_col = pl.col("open").cast(pl.Float64)
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        volume = pl.col("volume").cast(pl.Float64)
        prev_close = close.shift(1).over("code")
        max_oc = pl.max_horizontal(open_col, close)
        min_oc = pl.min_horizontal(open_col, close)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                prev_close.alias("prev_close"),
                # 用滚动均量做粗筛（精筛阶段仍按原逻辑复核）
                volume.shift(1)
                .rolling_mean(window_size=self.selector.vol_lookback_n, min_samples=max(3, int(self.selector.vol_lookback_n * 0.6)))
                .over("code")
                .alias("avg_vol_prev"),
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("ZXDQ"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= need_len)
                & (pl.col("prev_close") > 0)
                & (close > 0)
                & (high >= max_oc)
                & (low <= min_oc)
                & (((close / pl.col("prev_close")) - 1.0) > self.selector.up_pct_threshold)
                & (((high - max_oc) / max_oc) < self.selector.upper_wick_pct_max)
                & (pl.col("avg_vol_prev") > 0)
                & (volume >= self.selector.vol_multiple * pl.col("avg_vol_prev"))
                & (pl.col("ZXDQ").is_not_null())
                & (close < pl.col("ZXDQ") * self.selector.close_lt_zxdq_mult)
                & ((close >= open_col) if self.selector.require_bullish_close else pl.lit(True))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


def build_strategy_runner(selector: Any) -> StrategySelectionRunner:
    """根据 selector 类型构建对应 Runner。"""
    if isinstance(selector, BBIKDJSelector):
        return BBIKDJSelectionRunner(selector)
    if isinstance(selector, SuperB1Selector):
        return SuperB1SelectionRunner(selector)
    if isinstance(selector, PeakKDJSelector):
        return PeakKDJSelectionRunner(selector)
    if isinstance(selector, BBIShortLongSelector):
        return BBIShortLongSelectionRunner(selector)
    if isinstance(selector, MA60CrossVolumeWaveSelector):
        return MA60CrossVolumeWaveSelectionRunner(selector)
    if isinstance(selector, BigBullishVolumeSelector):
        return BigBullishVolumeSelectionRunner(selector)
    return DefaultSelectionRunner(selector)


# ─────────────────────────── 结果输出 ─────────────────────────── #

def print_strategy_result(
    strategy_name: str,
    emoji: str,
    picks: List[str],
    elapsed: float,
    date: str,
) -> None:
    """打印单个策略的选股结果"""
    # 创建结果表格
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="bright_blue",
        title=f"{emoji} {strategy_name} 选股结果",
        title_style="bold magenta",
        show_lines=True,
    )
    table.add_column("序号", justify="center", style="bold", width=6)
    table.add_column("股票代码", justify="center", style="bold cyan", width=12)
    table.add_column("状态", justify="center", width=6)
    
    if picks:
        for i, code in enumerate(picks, 1):
            table.add_row(str(i), code, "✅")
    else:
        table.add_row("-", "无符合条件的股票", "-")
    
    console.print(table)
    
    # 打印统计信息
    stats_panel = Panel(
        f"[bold]策略名称:[/bold] {strategy_name}\n"
        f"[bold]交易日期:[/bold] {date}\n"
        f"[bold]选中数量:[/bold] {len(picks)} 只\n"
        f"[bold]耗时:[/bold] {elapsed:.3f} 秒",
        title="📈 选股统计",
        border_style="green",
        expand=False,
    )
    console.print(stats_panel)
    console.print()


def save_results(results: Dict, date: str, output_dir: str) -> str:
    """保存结果到 JSON 文件"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = date.replace("-", "")
    filepath = os.path.join(output_dir, f"{date_str}.json")
    
    # 读取已有结果（如果有）
    existing = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    
    # 合并结果
    existing.update(results)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    return filepath


# ─────────────────────────── 主程序 ─────────────────────────── #

def main():
    parser = argparse.ArgumentParser(description="选股程序 - Polars 版本")
    parser.add_argument("--date", required=True, help="选股日期 (YYYY-MM-DD)")
    parser.add_argument("--data-dir", default="db", help="Parquet 数据目录")
    parser.add_argument("--config", default="configs.json", help="Selector 配置文件")
    parser.add_argument("--output-dir", default="backtest_results", help="结果输出目录")
    parser.add_argument("--tickers", nargs="+", help="指定股票代码")
    parser.add_argument("--strategies", nargs="+", help="指定策略名称")
    
    args = parser.parse_args()
    
    # 验证日期格式
    try:
        date_obj = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        console.print("[red]❌ 日期格式错误，请使用 YYYY-MM-DD 格式[/red]")
        sys.exit(1)
    
    # 打印标题
    console.print()
    console.print(
        Panel(
            f"[bold cyan]选股日期:[/bold cyan] {args.date}\n"
            f"[bold cyan]数据目录:[/bold cyan] {args.data_dir}\n"
            f"[bold cyan]配置文件:[/bold cyan] {args.config}\n"
            f"[bold cyan]结果目录:[/bold cyan] {args.output_dir}",
            title="🚀 选股程序 (Polars 版本)",
            border_style="bright_blue",
            expand=False,
        )
    )
    console.print()
    
    # 加载数据
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("📊 加载数据...", total=None)
        start_time = time.time()
        data_table = load_data_polars_table(args.data_dir, args.tickers)
        load_time = time.time() - start_time
        progress.update(task, completed=True)
    
    stock_count = data_table["code"].n_unique() if not data_table.is_empty() else 0
    console.print(f"[green]✅ 成功加载 {stock_count} 只股票的数据 (耗时: {load_time:.2f} 秒)[/green]")
    console.print()

    # 从配置文件加载策略
    strategies = load_strategies_from_config(Path(args.config))
    
    # 确定要运行的策略
    if args.strategies:
        strategies_to_run = {k: v for k, v in strategies.items() if k in args.strategies}
    else:
        strategies_to_run = strategies
    
    if not strategies_to_run:
        console.print("[red]❌ 没有可运行的策略[/red]")
        sys.exit(1)
    
    # 运行选股
    all_results = {}
    total_start = time.time()
    data_dict_cache: Optional[Dict[str, pl.DataFrame]] = None

    def get_data_dict() -> Dict[str, pl.DataFrame]:
        """懒加载旧版 Dict 结构，避免不必要的数据拆分开销。"""
        nonlocal data_dict_cache
        if data_dict_cache is None:
            data_dict_cache = table_to_data_dict(data_table)
        return data_dict_cache
    
    for strategy_name, config in strategies_to_run.items():
        emoji = config["emoji"]
        selector = config["selector"]
        runner = build_strategy_runner(selector)
        
        console.print(f"[bold]正在运行: {emoji} {strategy_name}...[/bold]")
        
        start = time.time()
        picks = runner.run_selection(
            date_obj=date_obj,
            data_table=data_table,
            get_data_dict=get_data_dict,
        )
        elapsed = time.time() - start
        
        # 打印结果
        print_strategy_result(strategy_name, emoji, picks, elapsed, args.date)
        
        # 保存结果
        all_results[strategy_name] = {
            "date": args.date,
            "stocks": picks,
            "count": len(picks),
        }
    
    total_elapsed = time.time() - total_start
    
    # 保存所有结果
    filepath = save_results(all_results, args.date, args.output_dir)
    console.print(f"[green]💾 结果已保存到: {filepath}[/green]")
    console.print()
    
    # 打印总结
    console.print(
        Panel(
            f"[bold green]✅ 选股完成[/bold green]\n"
            f"[bold]总耗时:[/bold] {total_elapsed:.2f} 秒",
            border_style="green",
            expand=False,
        )
    )


if __name__ == "__main__":
    main()