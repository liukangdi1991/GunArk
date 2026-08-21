"""Deterministic baseline: fixed input -> exact selected_codes, captured BEFORE
the selector refactor so the optimization can prove behavior equivalence.

make_ohlcv_df uses np.random.seed(42), so the data is deterministic.
"""

import pytest
import polars as pl
from datetime import date

from .helpers import (
    make_bbi_kdj_b1_defn, make_super_b1_defn, make_bbi_short_long_defn,
    make_peak_kdj_defn, make_ma60_volume_wave_defn, make_zxdkx_balance_defn,
    make_perfect_b1_defn, make_big_bullish_volume_defn,
    make_volume_spike_balance_defn, make_empty_df, make_ohlcv_df,
)
from trendradar.domain.strategy.protocol import SelectionContext


def _ctx(df):
    return SelectionContext(
        trade_date=date(2026, 7, 9),
        market_data=df,
        candidate_codes=df["code"].unique().to_list(),
    )


def _select_codes(defn, df):
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    return sel.select_day(_ctx(df), warmup).selected_codes


# Two deterministic fixtures: one stock (000001) with 150 days uptrend,
# two stocks (000001, 600519) with different trends.
def _df_single():
    return make_ohlcv_df("000001", 150, trend=0.002)


def _df_double():
    a = make_ohlcv_df("000001", 150, trend=0.002)
    b = make_ohlcv_df("600519", 150, trend=0.001, start_price=100.0)
    return pl.concat([a, b])


# Exact selected_codes captured pre-refactor (2026-08-21) from the current
# select() implementations on the deterministic fixtures. The refactor must
# reproduce these lists bit-for-bit. Keyed by strategy_id + fixture name.
#
# Ordering note (spec v9 "行为等价的前提声明"): polars `unique()` (used by the
# current selectors) returns codes in non-deterministic order, so the raw
# selected_codes ORDER on the two-code fixture is not stable pre-refactor.
# The spec normalizes this away: the runner sorts codes and market_data
# (`sorted(codes)` + `sort(["code","date"])`), and the refactored selectors
# iterate partition_by groups in ascending order. The deterministic baseline
# is therefore authoritative in SORTED order ("在确定性断言基准内以「排序后」为准"):
# the assertion compares `sorted(codes)` against these exact sorted lists.
_EXPECTED = {
    "bbi_kdj_b1,_df_single": [],
    "super_b1,_df_single": [],
    "bbi_short_long,_df_single": ["000001"],
    "peak_kdj,_df_single": [],
    "ma60_volume_wave,_df_single": [],
    "zxdkx_balance,_df_single": [],
    "perfect_b1_v2,_df_single": [],
    "big_bullish_volume,_df_single": [],
    "volume_spike_balance,_df_single": [],
    "bbi_kdj_b1,_df_double": [],
    "super_b1,_df_double": [],
    "bbi_short_long,_df_double": ["000001", "600519"],
    "peak_kdj,_df_double": [],
    "ma60_volume_wave,_df_double": [],
    "zxdkx_balance,_df_double": [],
    "perfect_b1_v2,_df_double": [],
    "big_bullish_volume,_df_double": [],
    "volume_spike_balance,_df_double": [],
}


@pytest.mark.parametrize(
    "defn_maker, df_builder",
    [
        (make_bbi_kdj_b1_defn, _df_single),
        (make_super_b1_defn, _df_single),
        (make_bbi_short_long_defn, _df_single),
        (make_peak_kdj_defn, _df_single),
        (make_ma60_volume_wave_defn, _df_single),
        (make_zxdkx_balance_defn, _df_single),
        (make_perfect_b1_defn, _df_single),
        (make_big_bullish_volume_defn, _df_single),
        (make_volume_spike_balance_defn, _df_single),
        (make_bbi_kdj_b1_defn, _df_double),
        (make_super_b1_defn, _df_double),
        (make_bbi_short_long_defn, _df_double),
        (make_peak_kdj_defn, _df_double),
        (make_ma60_volume_wave_defn, _df_double),
        (make_zxdkx_balance_defn, _df_double),
        (make_perfect_b1_defn, _df_double),
        (make_big_bullish_volume_defn, _df_double),
        (make_volume_spike_balance_defn, _df_double),
    ],
)
def test_deterministic_baseline(defn_maker, df_builder):
    defn = defn_maker()
    codes = _select_codes(defn, df_builder())
    # Baseline captured pre-refactor; assert exact list so the refactor must
    # reproduce it bit-for-bit (sorted-normalized per spec v9, see _EXPECTED).
    assert sorted(codes) == _EXPECTED[f"{defn.strategy_id},{df_builder.__name__}"]
