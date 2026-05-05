from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
import inspect
import os
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import polars as pl

from selection.indicators import _compute_kdj_numba, bbi_deriv_uptrend
from selection.strategies import (
    BBIKDJSelector,
    SuperB1Selector,
    PeakKDJSelector,
    BBIShortLongSelector,
    MA60CrossVolumeWaveSelector,
    ZXDKXBalanceSelector,
    PerfectB1Selector,
    BigBullishVolumeSelector,
)

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


class DefaultSelectionRunner(StrategySelectionRunner):
    """通用 Runner：不做预筛，直接沿用策略原生 select。"""

    _has_zx_prefilter: bool = False
    _prefetch_indicators: List[str] = []

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        return None

    def _prefetch(
        self,
        data_table: pl.DataFrame,
        candidates: List[str],
        date_obj,
        need_len: int,
    ) -> pl.DataFrame:
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)

        cols_to_add = []

        if "KDJ" in self._prefetch_indicators:
            cols_to_add.extend([
                pl.col("low").rolling_min(window_size=9, min_samples=1).over("code").alias("_low_n"),
                pl.col("high").rolling_max(window_size=9, min_samples=1).over("code").alias("_high_n"),
            ])

        if "MA60" in self._prefetch_indicators:
            cols_to_add.append(close.rolling_mean(window_size=60, min_samples=1).over("code").alias("MA60"))

        if "DIF" in self._prefetch_indicators:
            cols_to_add.append(
                (close.ewm_mean(span=12, adjust=False).over("code")
                 - close.ewm_mean(span=26, adjust=False).over("code")).alias("DIF")
            )

        if "BBI" in self._prefetch_indicators:
            cols_to_add.append(
                (
                    close.rolling_mean(window_size=3).over("code")
                    + close.rolling_mean(window_size=6).over("code")
                    + close.rolling_mean(window_size=12).over("code")
                    + close.rolling_mean(window_size=24).over("code")
                ).truediv(4.0).alias("BBI")
            )

        if "ZX" in self._prefetch_indicators:
            cols_to_add.extend([
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("LONG_TERM_BULL_BEAR_LINE"),
            ])

        if "RSV" in self._prefetch_indicators:
            sel = self.selector
            n_short = sel.n_short if hasattr(sel, "n_short") else 3
            n_long = sel.n_long if hasattr(sel, "n_long") else 21
            low_short = low.rolling_min(window_size=n_short, min_samples=1).over("code")
            high_close_short = close.rolling_max(window_size=n_short, min_samples=1).over("code")
            low_long = low.rolling_min(window_size=n_long, min_samples=1).over("code")
            high_close_long = close.rolling_max(window_size=n_long, min_samples=1).over("code")
            cols_to_add.extend([
                ((close - low_short) / (high_close_short - low_short + 1e-9) * 100.0).alias(f"RSV_{n_short}"),
                ((close - low_long) / (high_close_long - low_long + 1e-9) * 100.0).alias(f"RSV_{n_long}"),
            ])

        if not cols_to_add:
            return data_table.filter(pl.col("code").is_in(candidates))

        result = (
            data_table.lazy()
            .filter((pl.col("date") <= date_obj) & (pl.col("code").is_in(candidates)))
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns(cols_to_add)
        )

        has_rsv = "RSV" in self._prefetch_indicators
        has_kdj = "KDJ" in self._prefetch_indicators
        if has_kdj:
            result = result.with_columns(
                ((close - pl.col("_low_n")) / (pl.col("_high_n") - pl.col("_low_n") + 1e-9) * 100).alias("_rsv")
            )

        result = result.collect()

        if has_kdj:
            kdj_cache = {}
            for key, df in result.partition_by("code", as_dict=True).items():
                code = key[0]
                rsv = df["_rsv"].to_numpy()
                K, D, J = _compute_kdj_numba(rsv)
                kdj_cache[code] = (K, D, J)
            result = result.drop(["_low_n", "_high_n", "_rsv"])

            new_rows = []
            for key, df in result.partition_by("code", as_dict=True).items():
                code = key[0]
                K, D, J = kdj_cache[code]
                df = df.with_columns([
                    pl.Series("K", K),
                    pl.Series("D", D),
                    pl.Series("J", J),
                ])
                new_rows.append(df)
            result = pl.concat(new_rows)

        return result

    def run_final_filter(
        self,
        *,
        date_obj,
        data_table: pl.DataFrame,
        candidates: Optional[List[str]],
        get_data_dict: Callable[[], Dict[str, pl.DataFrame]],
    ) -> List[str]:
        skip_day = candidates is not None
        skip_zx = skip_day and self._has_zx_prefilter
        if candidates is None:
            data = get_data_dict()
            return self.selector.select(date_obj, data)
        if not candidates:
            return []

        if self._prefetch_indicators:
            need_len = getattr(self, '_get_need_len', lambda: 150)()
            subset_table = self._prefetch(data_table, candidates, date_obj, need_len)
        else:
            subset_table = data_table.filter(pl.col("code").is_in(candidates))

        subset = {}
        for code, df in subset_table.partition_by("code", as_dict=True).items():
            subset[code[0]] = df.drop("code")

        if len(subset) > 50:
            return self._parallel_select(date_obj, subset, skip_day, skip_zx)
        return self.selector.select(date_obj, subset, skip_day_check=skip_day, skip_zx_check=skip_zx)

    def _parallel_select(self, date_obj, subset, skip_day, skip_zx):
        selector = self.selector
        passes_filters = getattr(selector, "_passes_filters", None)
        if passes_filters is None:
            return selector.select(date_obj, subset, skip_day_check=skip_day, skip_zx_check=skip_zx)

        sig = inspect.signature(passes_filters)
        params = sig.parameters
        supports_flags = "skip_day_check" in params and "skip_zx_check" in params

        def check_one(item):
            code, hist = item
            try:
                if supports_flags:
                    ok = passes_filters(hist, skip_day_check=skip_day, skip_zx_check=skip_zx)
                else:
                    ok = passes_filters(hist)
            except TypeError:
                ok = passes_filters(hist)
            if ok:
                return code
            return None
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            results = pool.map(check_one, subset.items())
        return [r for r in results if r is not None]


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
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("LONG_TERM_BULL_BEAR_LINE"),
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
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
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
        close = pl.col("close").cast(pl.Float64)

        candidates_hist = (
            data_table.lazy()
            .filter((pl.col("date") <= date_obj) & (pl.col("code").is_in(candidates)))
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                (
                    close.rolling_mean(window_size=3).over("code")
                    + close.rolling_mean(window_size=6).over("code")
                    + close.rolling_mean(window_size=12).over("code")
                    + close.rolling_mean(window_size=24).over("code")
                ).truediv(4.0).alias("BBI"),
                pl.col("low").rolling_min(window_size=9, min_samples=1).over("code").alias("_low_n"),
                pl.col("high").rolling_max(window_size=9, min_samples=1).over("code").alias("_high_n"),
            ])
            .with_columns([
                ((close - pl.col("_low_n")) / (pl.col("_high_n") - pl.col("_low_n") + 1e-9) * 100).alias("_rsv"),
            ])
            .collect()
        )

        rsv_by_code = candidates_hist.partition_by("code", as_dict=True)
        kdj_cache = {}
        for key, df in rsv_by_code.items():
            code = key[0]
            rsv = df["_rsv"].to_numpy()
            K, D, J = _compute_kdj_numba(rsv)
            kdj_cache[code] = (K, D, J)

        candidates_hist = candidates_hist.drop(["_low_n", "_high_n", "_rsv"])

        hist_by_code = {}
        for key, df in candidates_hist.partition_by("code", as_dict=True).items():
            hist_by_code[key[0]] = df.drop("code")

        def check_bbikdj(item):
            code, hist = item
            if hist.is_empty():
                return None
            if not bbi_deriv_uptrend(
                hist["BBI"],
                min_window=self.selector.bbi_min_window,
                max_window=self.selector.max_window,
                q_threshold=self.selector.bbi_q_threshold,
            ):
                return None
            K, D, J = kdj_cache[code]
            j_today = float(J[-1])
            j_arr = J[self.selector.max_window * -1 or len(J):]
            j_arr = j_arr[np.isfinite(j_arr)]
            if len(j_arr) == 0:
                return None
            j_quantile = float(np.percentile(j_arr, self.selector.j_q_threshold * 100, interpolation="linear"))
            if not (j_today < self.selector.j_threshold or j_today <= j_quantile):
                return None
            return code

        if len(hist_by_code) > 50:
            with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
                results = pool.map(check_bbikdj, hist_by_code.items())
            return [r for r in results if r is not None]

        picks: List[str] = []
        for code in candidates:
            hist = hist_by_code.get(code)
            if hist is None or hist.is_empty():
                continue
            r = check_bbikdj((code, hist))
            if r is not None:
                picks.append(r)
        return picks


class SuperB1SelectionRunner(DefaultSelectionRunner):
    """SuperB1 Runner：Polars 预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = []

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
    _has_zx_prefilter: bool = True
    _prefetch_indicators: List[str] = ["KDJ"]

    selector: PeakKDJSelector

    def _get_need_len(self) -> int:
        return self.selector.max_window + 20

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
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("LONG_TERM_BULL_BEAR_LINE"),
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
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class BBIShortLongSelectionRunner(DefaultSelectionRunner):
    """BBIShortLong Runner：Polars 预筛 + 原逻辑精筛。"""
    _has_zx_prefilter: bool = True
    _prefetch_indicators: List[str] = ["BBI", "RSV", "DIF"]

    selector: BBIShortLongSelector

    def _get_need_len(self) -> int:
        sel = self.selector
        return max(
            max(sel.n_short, sel.n_long) + sel.bbi_min_window + sel.m,
            sel.max_window,
        )

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
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("LONG_TERM_BULL_BEAR_LINE"),
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
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class MA60CrossVolumeWaveSelectionRunner(DefaultSelectionRunner):
    """MA60CrossVolumeWave Runner：Polars 预筛 + 原逻辑精筛。"""
    _has_zx_prefilter: bool = True
    _prefetch_indicators: List[str] = ["KDJ", "MA60"]

    selector: MA60CrossVolumeWaveSelector

    def _get_need_len(self) -> int:
        sel = self.selector
        return max(60 + sel.lookback_n + sel.ma60_slope_days, sel.max_window + 20)

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
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
                (
                    close.rolling_mean(window_size=14, min_samples=14).over("code")
                    + close.rolling_mean(window_size=28, min_samples=28).over("code")
                    + close.rolling_mean(window_size=57, min_samples=57).over("code")
                    + close.rolling_mean(window_size=114, min_samples=114).over("code")
                ).truediv(4.0).alias("LONG_TERM_BULL_BEAR_LINE"),
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
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null())
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
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
                close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code").alias("SHORT_TERM_TREND_LINE"),
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
                & (pl.col("SHORT_TERM_TREND_LINE").is_not_null())
                & (close < pl.col("SHORT_TERM_TREND_LINE") * self.selector.close_lt_short_term_trend_line_mult)
                & ((close >= open_col) if self.selector.require_bullish_close else pl.lit(True))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class ZXDKXBalanceSelectionRunner(DefaultSelectionRunner):
    """ZXDKXBalance Runner：Polars 预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = ["ZX"]

    selector: ZXDKXBalanceSelector

    def _get_need_len(self) -> int:
        return max(self.selector.recent_volume_window, 114) + 20

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self._get_need_len()
        close = pl.col("close").cast(pl.Float64)
        volume = pl.col("volume").cast(pl.Float64)
        short_term_trend_line = close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code")
        long_term_bull_bear_line = (
            close.rolling_mean(window_size=14, min_samples=14).over("code")
            + close.rolling_mean(window_size=28, min_samples=28).over("code")
            + close.rolling_mean(window_size=57, min_samples=57).over("code")
            + close.rolling_mean(window_size=114, min_samples=114).over("code")
        ).truediv(4.0)

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns([
                pl.len().over("code").alias("hist_len"),
                short_term_trend_line.alias("SHORT_TERM_TREND_LINE"),
                long_term_bull_bear_line.alias("LONG_TERM_BULL_BEAR_LINE"),
                volume.rolling_min(
                    window_size=self.selector.recent_volume_window,
                    min_samples=self.selector.recent_volume_window,
                ).over("code").alias("recent_vol_min"),
            ])
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= max(self.selector.recent_volume_window, 114))
                & pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null()
                & pl.col("SHORT_TERM_TREND_LINE").is_not_null()
                & (pl.col("LONG_TERM_BULL_BEAR_LINE").abs() > 1e-12)
                & (
                    (pl.col("LONG_TERM_BULL_BEAR_LINE") - pl.col("SHORT_TERM_TREND_LINE")).abs()
                    / pl.col("LONG_TERM_BULL_BEAR_LINE").abs()
                    < self.selector.zx_stick_limit_threshold
                )
                & (
                    close
                    >= pl.col("LONG_TERM_BULL_BEAR_LINE")
                    * self.selector.close_vs_long_term_bull_bear_line_limit_threshold
                )
                & pl.col("recent_vol_min").is_not_null()
                & (volume <= pl.col("recent_vol_min"))
            )
            .select("code")
            .collect()
        )
        return filtered["code"].to_list() if not filtered.is_empty() else []


class PerfectB1SelectionRunner(DefaultSelectionRunner):
    """PerfectB1 Runner：Polars 重预筛 + 原逻辑精筛。"""
    _prefetch_indicators: List[str] = ["KDJ", "MA60", "ZX"]

    selector: PerfectB1Selector

    def _get_need_len(self) -> int:
        return max(
            self.selector.ma_window + 20,
            self.selector.volume_spike_lookback + 30,
            self.selector.zx_long_window + 20,
            self.selector.volume_step_down_window + 20 if self.selector.volume_step_down_window > 0 else 0,
            self.selector.recent_volume_new_low_window + 20 if self.selector.recent_volume_new_low_window > 0 else 0,
        )

    def run_prefilter(self, *, date_obj, data_table: pl.DataFrame) -> Optional[List[str]]:
        if data_table.is_empty():
            return []

        need_len = self._get_need_len()
        close = pl.col("close").cast(pl.Float64)
        high = pl.col("high").cast(pl.Float64)
        low = pl.col("low").cast(pl.Float64)
        volume = pl.col("volume").cast(pl.Float64)

        prev_close = close.shift(1).over("code")
        prev_volume = volume.shift(1).over("code")

        short_term_trend_line = close.ewm_mean(span=10, adjust=False).ewm_mean(span=10, adjust=False).over("code")
        long_term_bull_bear_line = (
            close.rolling_mean(window_size=14, min_samples=14).over("code")
            + close.rolling_mean(window_size=28, min_samples=28).over("code")
            + close.rolling_mean(window_size=57, min_samples=57).over("code")
            + close.rolling_mean(window_size=114, min_samples=114).over("code")
        ).truediv(4.0)

        spike_flag = (
            (close > prev_close)
            & (prev_volume > 0)
            & (volume > prev_volume * self.selector.volume_spike_multiple)
        ).cast(pl.Int8)

        cols = [
            pl.len().over("code").alias("hist_len"),
            prev_close.alias("prev_close"),
            close.rolling_mean(window_size=self.selector.ma_window, min_samples=1).over("code").alias("MA_N"),
            short_term_trend_line.alias("SHORT_TERM_TREND_LINE"),
            long_term_bull_bear_line.alias("LONG_TERM_BULL_BEAR_LINE"),
            spike_flag.rolling_max(
                window_size=self.selector.volume_spike_lookback,
                min_samples=1,
            ).over("code").alias("has_spike"),
        ]

        if self.selector.volume_step_down_window > 0:
            dec_flag = (volume < prev_volume).cast(pl.Int16)
            cols.append(
                dec_flag.rolling_sum(
                    window_size=self.selector.volume_step_down_window,
                    min_samples=self.selector.volume_step_down_window,
                ).over("code").alias("step_down_count")
            )

        if self.selector.recent_volume_new_low_window > 0:
            cols.append(
                volume.rolling_min(
                    window_size=self.selector.recent_volume_new_low_window,
                    min_samples=self.selector.recent_volume_new_low_window,
                ).over("code").alias("recent_vol_min")
            )

        filtered = (
            data_table.lazy()
            .filter(pl.col("date") <= date_obj)
            .group_by("code", maintain_order=True)
            .tail(need_len)
            .sort(["code", "date"])
            .with_columns(cols)
            .group_by("code", maintain_order=True)
            .tail(1)
            .filter(
                (pl.col("hist_len") >= max(
                    self.selector.ma_window,
                    self.selector.volume_spike_lookback + 1,
                    self.selector.zx_long_window,
                    self.selector.volume_step_down_window + 1 if self.selector.volume_step_down_window > 0 else 0,
                    self.selector.recent_volume_new_low_window if self.selector.recent_volume_new_low_window > 0 else 0,
                    3,
                ))
                & (pl.col("prev_close") > 0)
                & (low > 0)
                & (((high - low) / low) < self.selector.amplitude_limit)
                & ((close / pl.col("prev_close") - 1.0) > self.selector.pct_chg_lower)
                & ((close / pl.col("prev_close") - 1.0) < self.selector.pct_chg_upper)
                & pl.col("LONG_TERM_BULL_BEAR_LINE").is_not_null()
                & pl.col("SHORT_TERM_TREND_LINE").is_not_null()
                & (close > pl.col("MA_N"))
                & (close > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("SHORT_TERM_TREND_LINE") > pl.col("LONG_TERM_BULL_BEAR_LINE"))
                & (pl.col("has_spike") > 0)
                & (
                    (pl.col("step_down_count") >= self.selector.min_volume_step_down_days)
                    if self.selector.volume_step_down_window > 0
                    else pl.lit(True)
                )
                & (
                    (volume <= pl.col("recent_vol_min"))
                    if self.selector.recent_volume_new_low_window > 0
                    else pl.lit(True)
                )
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
    if isinstance(selector, ZXDKXBalanceSelector):
        return ZXDKXBalanceSelectionRunner(selector)
    if isinstance(selector, PerfectB1Selector):
        return PerfectB1SelectionRunner(selector)
    if isinstance(selector, BigBullishVolumeSelector):
        return BigBullishVolumeSelectionRunner(selector)
    return DefaultSelectionRunner(selector)
