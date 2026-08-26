"""B1战法V2 除权处理：通达信 REF(C,1) 使用可比昨收（pre_close）。

除权日价格跳空：C 相对原始昨收必然下跌（假阳/假阴），但相对可比昨收
（pre_close = 昨收×除权因子）可能收涨（真阳）。通达信默认"除权处理"，
REF(C,1) 返回可比昨收，K 线本身用实际价。本测试验证公式模块按
pre_close 语义分类。
"""
from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.formulas.b1_v2 import compute_b1_v2_columns


def _df_with_pre_close(rows: list[dict]) -> pl.DataFrame:
    n = len(rows)
    d0 = date(2026, 7, 1)
    return pl.DataFrame({
        "code": ["600001"] * n,
        "date": [d0 + timedelta(days=i) for i in range(n)],
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
        "volume": [r["volume"] for r in rows],
        "pre_close": [r["pre_close"] for r in rows],
    })


def test_ex_dividend_day_real_yang_uses_pre_close():
    """除权日：原始昨收下是假阳（C<REF），可比昨收下是真阳（C>pre_close）。

    通达信除权处理 → REF(C,1)=pre_close → REAL_YANG=1，量计入阳量。
    """
    rows = [
        {"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1e6, "pre_close": 10.0},
        {"open": 10.2, "high": 10.6, "low": 10.1, "close": 10.4, "volume": 1e6, "pre_close": 10.2},
        # 除权日：实际价跳空下跌（C=9.8 < 原始昨收 10.4），但相对可比昨收 9.7 收涨
        {"open": 9.5, "high": 9.9, "low": 9.4, "close": 9.8, "volume": 2e6, "pre_close": 9.7},
    ]
    df = _df_with_pre_close(rows)
    out = compute_b1_v2_columns(df)
    g = out.partition_by("code")[0]

    # 除权日（最后一行）：C>O 且 C>pre_close → 真阳（计入阳量）
    assert g["real_yang"][-1] is True, "除权处理下除权日应为真阳线"
    assert g["real_yin"][-1] is False


def test_ex_dividend_day_without_pre_close_falls_back_to_shift():
    """无 pre_close 列（旧数据）：回退为原始昨收 shift，除权日保持假阳。"""
    rows = [
        {"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1e6, "pre_close": 10.0},
        {"open": 10.2, "high": 10.6, "low": 10.1, "close": 10.4, "volume": 1e6, "pre_close": 10.2},
        {"open": 9.5, "high": 9.9, "low": 9.4, "close": 9.8, "volume": 2e6, "pre_close": 9.7},
    ]
    # 去掉 pre_close 列 → 用 shift(1)：C=9.8 < 原始昨收 10.4 → 假阳
    df = _df_with_pre_close(rows).drop("pre_close")
    out = compute_b1_v2_columns(df)
    g = out.partition_by("code")[0]
    assert g["real_yang"][-1] is False, "无 pre_close 时应回退原始昨收（假阳）"


def test_volume_ratio_includes_ex_day_yang_volume():
    """除权日按真阳计入阳量：14 日阳/阴比值随之变化（区别于排除）。"""
    rows = []
    # 13 天阴线（小量）
    for i in range(13):
        rows.append({"open": 10.0, "high": 10.2, "low": 9.8, "close": 9.9, "volume": 1e5,
                     "pre_close": 10.0})
    # 除权日：真阳 + 大阳量（pre_close 语义）
    rows.append({"open": 9.5, "high": 9.9, "low": 9.4, "close": 9.8, "volume": 5e5,
                 "pre_close": 9.7})
    df = _df_with_pre_close(rows)
    out = compute_b1_v2_columns(df)
    g = out.partition_by("code")[0]
    # 阳量应包含除权日 5e5（真阳），阴量 13×1e5
    yang_v = sum(g["volume"][t] for t in range(g.height) if g["real_yang"][t])
    yin_v = sum(g["volume"][t] for t in range(g.height) if g["real_yin"][t])
    assert yang_v == 5e5, f"阳量应含除权日，实际 {yang_v}"
    assert yin_v == 13 * 1e5
    assert yang_v / yin_v < 2.25  # 比值本身不达标，但阳量归属正确
