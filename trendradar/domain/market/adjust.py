"""qfq 守卫与缩放（kline 与 zx 系选股共用）。语义钉住 strategy/formulas/b1.py:58
_qfq_scale，守卫有意更严：列内 null 整列退化（参照副本是逐行跳过），差异被测试钉住。"""
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


def _identity_holds(df: pl.DataFrame, tolerance: float = 0.01) -> bool:
    """前收盘恒等式校验（已重建数据的因子真实性判据）。

    除权恒等式：pre_close_t × f_t = close_{t−1} × f_{t−1}（可比昨收定义）。
    因子真实时对送转/配股等任意大比例跳变都成立（002747 实测 3.02× 配股跳变，
    恒等式误差 0.03%）；1.0 占位污染则必破。首根及 pre_close ≤ 0 / null 的
    相邻对跳过（已重建数据 pre_close 零空值，跳过面仅首根）。tolerance=1%：
    Tushare pre_close 精确到分，实际误差 <0.05%。
    """
    prev_close = df["close"].shift(1)
    prev_f = df["adj_factor"].shift(1)
    expected = prev_close * prev_f / df["adj_factor"]
    err = (df["pre_close"] - expected).abs() / expected
    valid = expected.is_not_null() & (expected > 0) & df["pre_close"].is_not_null()
    return not bool((err.filter(valid) > tolerance).any())


def apply_qfq(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """前复权：scale = adj_factor / 最新因子，OHLC 与 pre_close × scale（R3）；
    volume/amount 永不缩放。返回 (缩放后 df, adjust_degraded)。

    守卫整列语义（任一命中 → 整列退化原价，禁止部分缩放）：
    1. 基础守卫（两态常开）：因子列缺失 / 含 null / ≤0 / NaN
    2. 已重建（重建标志成立）：前收盘恒等式校验——因子真实时对配股等大比例
       跳变同样成立，占位 1.0 污染则必破
    3. 未重建（重建标志不成立）：恒 1.0；含 1.0 与非 1.0 混合；相邻比越带
       （>3× 或 <1/3×）——存量启发式，仅服务污染检测，不约束真实因子
    """
    if "adj_factor" not in df.columns:
        return df, True
    factors = df["adj_factor"]
    if factors.null_count() or factors.is_nan().any() or (factors <= 0).any():
        return df, True
    latest = factors[-1]
    if _rebuild_marker(df):
        if not _identity_holds(df):
            return df, True
    else:
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


def apply_qfq_grouped(df: pl.DataFrame, tolerance: float = 0.01) -> tuple[pl.DataFrame, bool]:
    """多票拼接帧的向量化前复权（选股侧专用）：守卫/缩放以 code 为域一次成列。

    性能契约（复审性能项）：O(1) 次 polars 调用替代 O(N_code) 次逐段
    apply_qfq——5000 票全市场帧逐段版 ~6.6s/次，本版毫秒级。段级语义与
    apply_qfq 完全一致（基础守卫/重建标志/恒等式/存量三守卫），任一段守卫
    命中 → 该段整段退化原价，段间互不影响；degraded=任一段退化。

    行序契约：输入须按 (code, date) 排序——组内 shift(1)/last 的行序依赖它
    （唯一选股入口 _run_selection 已 sort(["code","date"])）；返回帧保持该序。
    """
    if df.is_empty() or "adj_factor" not in df.columns:
        return df, True
    f = pl.col("adj_factor")
    g = "code"
    factor_bad = (f.is_null() | f.is_nan() | (f <= 0)).any().over(g)
    if "pre_close" in df.columns:
        rebuild = (pl.col("pre_close").is_null().sum().over(g) <= 1)
        expected = pl.col("close").shift(1).over(g) * f.shift(1).over(g) / f
        row_bad = (
            (expected.is_not_null() & (expected > 0) & pl.col("pre_close").is_not_null())
            & ((pl.col("pre_close") - expected).abs() / expected > tolerance)
        )
        identity_bad = row_bad.any().over(g)
        legacy_bad = pl.lit(False)
    else:
        rebuild = pl.lit(False)
        identity_bad = pl.lit(False)
        legacy_bad = (
            (f == 1.0).all().over(g)
            | ((f == 1.0).any().over(g) & (f != 1.0).any().over(g))
        ) | (
            ((f / f.shift(1).over(g)) > 3.0) | ((f / f.shift(1).over(g)) < 1.0 / 3.0)
        ).any().over(g)
    seg_bad = factor_bad | pl.when(rebuild).then(identity_bad).otherwise(legacy_bad)
    degraded = bool(df.select(seg_bad.any().alias("any"))["any"][0])
    scale_eff = pl.when(seg_bad).then(pl.lit(1.0)).otherwise(f / f.last().over(g))
    out = df.with_columns(scale_eff.alias("_scale")).with_columns(
        (pl.col(c) * pl.col("_scale")).alias(c)
        for c in ("open", "high", "low", "close", "pre_close") if c in df.columns
    ).drop("_scale")
    return out, degraded
