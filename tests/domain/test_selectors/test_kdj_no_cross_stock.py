"""Recursive ewm indicators (KDJ, DIF) must be computed per code.

When computed on the flat concatenated market table, the recursive state of
one stock leaks into the first rows of the next — a short-history stock's J/DIF
is partly inherited from the previous stock.
"""

import pytest
import polars as pl

from trendradar.domain.strategy.formulas.kdj import compute_kdj
from trendradar.domain.strategy.formulas.ma import compute_dif
from .helpers import make_super_b1_defn, make_bbi_kdj_b1_defn, make_ohlcv_df


def test_super_b1_warmup_kdj_not_cross_stock_contaminated():
    rising = make_ohlcv_df("000001", 60, trend=0.01)
    short = make_ohlcv_df("000002", 3, trend=-0.02)
    combined = pl.concat([rising, short])

    defn = make_super_b1_defn()
    w = defn.selector_class(defn).warmup(combined)

    _, _, j_alone = compute_kdj(short)
    assert w.grouped["000002"]["j"].to_list() == pytest.approx(
        j_alone.to_list(), rel=1e-9
    )


def test_bbi_kdj_b1_warmup_not_cross_stock_contaminated():
    """B1 公式全量按 code 分组计算：短历史股票不受前一只股票污染。"""
    rising = make_ohlcv_df("000001", 60, trend=0.01)
    short = make_ohlcv_df("000002", 3, trend=-0.02)
    combined = pl.concat([rising, short])

    defn = make_bbi_kdj_b1_defn()
    sel = defn.selector_class(defn)
    w_combined = sel.warmup(combined)
    w_alone = sel.warmup(short)

    assert w_combined.grouped["000002"]["_b1_signal"].to_list() == pytest.approx(
        w_alone.grouped["000002"]["_b1_signal"].to_list(), rel=1e-9
    )
