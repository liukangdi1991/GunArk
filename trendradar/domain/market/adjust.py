"""qfq 守卫与缩放（kline 专用）。语义钉住 strategy/formulas/b1.py:58 _qfq_scale，
守卫有意更严：列内 null 整列退化（参照副本是逐行跳过），差异被 §7 测试钉住。"""
from __future__ import annotations

import polars as pl


def _rebuild_marker(df: pl.DataFrame) -> bool:
    """重建标志 = 文件含 pre_close 列且 null_count ≤ 1（R7，复用 spec §1 判据）。

    仅判「含列」会被重建前的常规增量同步击穿：_align_columns 把旧行 pre_close 补
    null、新行带真值，合并文件从此「含列」且 adj_factor 恰成混合列。阈值失效方向
    是误报 degraded（可见），不是漏报。
    """
    if "pre_close" not in df.columns:
        return False
    return df["pre_close"].null_count() <= 1


def apply_qfq(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """前复权：scale = adj_factor / 最新因子，OHLC 与 pre_close × scale（R3）；
    volume/amount 永不缩放。返回 (缩放后 df, adjust_degraded)。

    守卫整列语义（任一命中 → 整列退化原价，禁止部分缩放）：
    基础守卫（两态常开）：因子列缺失 / 含 null / ≤0 / NaN / 相邻比越带(>3× 或 <1/3×)；
    1.0 特征守卫（仅重建标志不成立）：恒 1.0；含 1.0 与非 1.0 混合。
    """
    if "adj_factor" not in df.columns:
        return df, True
    factors = df["adj_factor"]
    if factors.null_count() or factors.is_nan().any() or (factors <= 0).any():
        return df, True
    latest = factors[-1]
    if not _rebuild_marker(df):
        if factors.n_unique() == 1 and factors[0] == 1.0:
            return df, True
        if (factors == 1.0).any() and (factors != 1.0).any():
            return df, True
    ratio = factors / factors.shift(1)
    bounded = ratio.drop_nulls()
    if (bounded > 3.0).any() or (bounded < (1.0 / 3.0)).any():
        return df, True
    scaled = df.with_columns(
        (pl.col(c) * (pl.col("adj_factor") / latest)).alias(c)
        for c in ("open", "high", "low", "close", "pre_close")
        if c in df.columns
    )
    return scaled, False
