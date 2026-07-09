from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import os
from typing import Any, Callable, Dict, List, Optional

import polars as pl

from selection.formulas.indicators import _compute_kdj_numba
from selection.execution.base import StrategySelectionRunner
from selection.formulas.expressions import (
    bbi_line,
    close_expr,
    dif_line,
    high_expr,
    long_term_bull_bear_line,
    low_expr,
    moving_average,
    rolling_max,
    rolling_min,
    rsv_from_range,
    rsv_high_bound,
    rsv_low_bound,
    short_term_trend_line,
)


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
        close = close_expr()
        high = high_expr()
        low = low_expr()

        cols_to_add = []

        if "KDJ" in self._prefetch_indicators:
            cols_to_add.extend([
                rolling_min(low, 9).alias("_low_n"),
                rolling_max(high, 9).alias("_high_n"),
            ])

        if "MA60" in self._prefetch_indicators:
            cols_to_add.append(moving_average(close, 60).alias("MA60"))

        if "DIF" in self._prefetch_indicators:
            cols_to_add.append(dif_line(close).alias("DIF"))

        if "BBI" in self._prefetch_indicators:
            cols_to_add.append(bbi_line(close).alias("BBI"))

        if "ZX" in self._prefetch_indicators:
            cols_to_add.extend([
                short_term_trend_line(close).alias("SHORT_TERM_TREND_LINE"),
                long_term_bull_bear_line(close).alias("LONG_TERM_BULL_BEAR_LINE"),
            ])

        if "RSV" in self._prefetch_indicators:
            sel = self.selector
            n_short = sel.n_short if hasattr(sel, "n_short") else 3
            n_long = sel.n_long if hasattr(sel, "n_long") else 21
            low_short = rsv_low_bound(low, n_short)
            high_close_short = rsv_high_bound(close, n_short)
            low_long = rsv_low_bound(low, n_long)
            high_close_long = rsv_high_bound(close, n_long)
            cols_to_add.extend([
                rsv_from_range(close, low_short, high_close_short).alias(f"RSV_{n_short}"),
                rsv_from_range(close, low_long, high_close_long).alias(f"RSV_{n_long}"),
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
                rsv_from_range(close, pl.col("_low_n"), pl.col("_high_n")).alias("_rsv")
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
