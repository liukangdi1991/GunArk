"""B1战法（bbi_kdj_b1）除权处理验证。

通达信 REF(C,1) 在除权日应使用可比昨收（pre_close）而非原始昨收——
与 B1V2（perfect_b1_v2）保持一致。直接验证 _prev_close_expr 的取列逻辑，
并回归验证无 pre_close 列时随机数据与 shift 参考一致。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.formulas import b1 as b1_mod
from trendradar.domain.strategy.formulas.b1 import compute_b1_columns


def test_prev_close_uses_pre_close_when_present():
    """有 pre_close 列：REF(C,1) 取可比昨收（该行 pre_close），非原始昨收。"""
    df = pl.DataFrame({
        "close": [20.0, 10.5],
        "pre_close": [None, 10.0],  # 除权日可比昨收 10（10 送 10），原始昨收是 20
    })
    expr = b1_mod._prev_close_expr(df)
    got = df.with_columns(expr.alias("pc"))["pc"].to_list()
    assert got[0] is None            # 首日无昨收
    assert got[1] == 10.0            # 用可比昨收 10，而不是 close.shift 的 20


def test_prev_close_falls_back_to_shift_without_column():
    """无 pre_close 列（旧数据）：回退原始昨收 close.shift(1)。"""
    df = pl.DataFrame({"close": [20.0, 10.5]})
    expr = b1_mod._prev_close_expr(df)
    got = df.with_columns(expr.alias("pc"))["pc"].to_list()
    assert got[0] is None
    assert got[1] == 20.0


def test_random_matches_shift_reference_without_pre_close():
    """无 pre_close 列时：随机数据与 shift（原始昨收）参考逐日一致。"""
    import numpy as np

    rng = np.random.default_rng(7)
    rows = []
    start = date(2026, 1, 1)
    for i in range(3):
        n = int(rng.integers(60, 180))
        close = 10.0 * np.exp(np.cumsum(0.01 * rng.standard_normal(n)))
        open_ = close * (1 + 0.01 * rng.standard_normal(n))
        high = np.maximum(open_, close) * (1 + 0.01 * np.abs(rng.standard_normal(n)))
        low = np.minimum(open_, close) * (1 - 0.01 * np.abs(rng.standard_normal(n)))
        vol = 1e6 * (1 + 0.5 * rng.standard_normal(n))
        vol = np.maximum(vol, 1e3)
        for d in range(n):
            rows.append({
                "code": f"{i:06d}", "date": start + timedelta(days=d),
                "open": float(open_[d]), "high": float(high[d]),
                "low": float(low[d]), "close": float(close[d]),
                "volume": float(vol[d]),
            })
    df = pl.DataFrame(rows)

    from .test_bbi_kdj_b1_formula import _reference_hit

    prod = compute_b1_columns(df)
    ref = _reference_hit(df)  # 该参考用原始昨收（shift）
    for code, hit in ref.items():
        got = prod.filter(pl.col("code") == code)["_b1_signal"].to_list()
        assert got[-1] == hit, f"last-day mismatch for {code}"
