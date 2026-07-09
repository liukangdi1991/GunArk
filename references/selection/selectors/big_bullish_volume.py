from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from selection.formulas.indicators import (
    _find_peaks,
    bbi_deriv_uptrend,
    compute_bbi,
    compute_dif,
    compute_kdj,
    compute_rsv,
    compute_zx_lines,
    last_valid_ma_cross_up,
    passes_day_constraints_today,
    zx_condition_at_positions,
)

class BigBullishVolumeSelector:    
    """暴力K战法"""

    def __init__(
        self,
        *,
        up_pct_threshold: float = 0.04,
        upper_wick_pct_max: float = 0.5,
        vol_lookback_n: int = 20,
        vol_multiple: float = 1.5,
        min_history: int | None = None,
        require_bullish_close: bool = True,
        ignore_zero_volume: bool = True,
        close_lt_short_term_trend_line_mult: float = 1.0
    ) -> None:
        if up_pct_threshold <= 0:
            raise ValueError("up_pct_threshold 应 > 0")
        if upper_wick_pct_max < 0:
            raise ValueError("upper_wick_pct_max 应 >= 0")
        if vol_lookback_n < 1:
            raise ValueError("vol_lookback_n 应 >= 1")
        if vol_multiple <= 0:
            raise ValueError("vol_multiple 应 > 0")
        if close_lt_short_term_trend_line_mult <= 0:
            raise ValueError("close_lt_short_term_trend_line_mult 应 > 0")

        self.up_pct_threshold = float(up_pct_threshold)
        self.upper_wick_pct_max = float(upper_wick_pct_max)
        self.vol_lookback_n = int(vol_lookback_n)
        self.vol_multiple = float(vol_multiple)
        self.require_bullish_close = bool(require_bullish_close)
        self.ignore_zero_volume = bool(ignore_zero_volume)
        self.close_lt_short_term_trend_line_mult = float(close_lt_short_term_trend_line_mult)
        self.eps = float(1e-12)        
        self.min_history = int(min_history) if min_history is not None else (self.vol_lookback_n + 2)
        

    @staticmethod
    def _to_float(x) -> float:
        try:
            return float(x)
        except Exception:
            return float("nan")

    def _upper_wick_pct(self, o: float, h: float, c: float) -> float:
        return (h - max(o, c)) / max(o, c)

    def _passes_filters(self, hist: pl.DataFrame) -> bool:
        if hist is None or hist.is_empty():
            return False

        hist = hist.sort("date")

        if len(hist) < self.min_history:
            return False
        if len(hist) < (self.vol_lookback_n + 2):
            return False

        today = hist.tail(1)
        prev = hist.tail(2).head(1)

        oT = self._to_float(today["open"][0])
        hT = self._to_float(today["high"][0])
        lT = self._to_float(today["low"][0])
        cT = self._to_float(today["close"][0])
        vT = self._to_float(today["volume"][0])

        cP = self._to_float(prev["close"][0])

        if not (np.isfinite(oT) and np.isfinite(hT) and np.isfinite(lT) and np.isfinite(cT) and np.isfinite(vT) and np.isfinite(cP)):
            return False
        if cP <= 0 or cT <= 0:
            return False
        if hT < max(oT, cT) or lT > min(oT, cT):
            return False

        if self.require_bullish_close and not (cT >= oT):
            return False

        pct_chg = cT / cP - 1.0
        if pct_chg <= self.up_pct_threshold:
            return False

        wick_pct = self._upper_wick_pct(oT, hT, cT)
        if not np.isfinite(wick_pct):
            return False
        if wick_pct >= self.upper_wick_pct_max:
            return False

        vol_hist = hist[-(self.vol_lookback_n + 1):-1]["volume"].cast(pl.Float64)
        if self.ignore_zero_volume:
            vol_hist = vol_hist.replace(0, None).drop_nulls()

        if len(vol_hist) < max(3, int(self.vol_lookback_n * 0.6)):
            return False

        avg_vol = float(vol_hist.mean())
        if not (np.isfinite(avg_vol) and avg_vol > 0):
            return False

        if vT < self.vol_multiple * avg_vol:
            return False
        
        try:
            short_term_trend_line, _ = compute_zx_lines(hist)
            short_term_trend_line_today = float(short_term_trend_line[-1])
        except Exception:
            short_term_trend_line_today = float("nan")

        if not np.isfinite(short_term_trend_line_today):
            return False
        else:
            if not (cT < short_term_trend_line_today * self.close_lt_short_term_trend_line_mult):
                return False

        return True

    def select(self, date, data: Dict[str, pl.DataFrame], skip_day_check: bool = False, skip_zx_check: bool = False) -> List[str]:
        picks: List[str] = []
        need_len = max(self.min_history, self.vol_lookback_n + 2)

        for code, df in data.items():
            if df is None or df.is_empty():
                continue
            hist = df.filter(pl.col("date") <= date).tail(need_len)
            if len(hist) < need_len:
                continue
            if self._passes_filters(hist):
                picks.append(code)

        return picks
