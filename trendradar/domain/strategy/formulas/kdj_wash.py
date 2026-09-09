"""知行洗盘线：多周期 KDJ 四线归零指标（用户 TDX 原文，2026-09-08）。

四周期强弱 RSV(N) = 100*(C−LLV(L,N))/(HHV(C,N)−LLV(L,N))，窗口 N1=3 / 10 / 20 / N2=21。
买入信号柱（−30，堆在 0 轴下方）：
- 四线归零买：四线全 ≤ 6
- 白线下20买：短期 ≤ 20 且 长期 ≥ 60
- 白穿红线买：CROSS(短期,长期) 且 长期 < 20
- 白穿黄线买：CROSS(短期,中期) 且 中期 < 30
CROSS(A,B) = A 当根 > B 且 前根 A ≤ B。分母为零（HHV==LLV 的极扁窗口）时 RSV 取 0。
"""
from __future__ import annotations

import polars as pl


def _rsv(close: pl.Series, low: pl.Series, high_close: pl.Series, n: int) -> pl.Series:
    """100*(C−LLV(L,N))/(HHV(C,N)−LLV(L,N))；HHV 取收盘、LLV 取最低（TDX 原文）。

    min_samples=1：TDX 窗口按可用根数收缩（首根即有值），与通达信行为一致。
    分母为零（HHV==LLV 的极扁窗口）时 RSV 取 0。
    """
    hhv_c = close.rolling_max(n, min_samples=1)
    llv_l = low.rolling_min(n, min_samples=1)
    denom = hhv_c - llv_l
    return (
        pl.when(denom != 0)
        .then((close - llv_l) / denom * 100)
        .otherwise(0.0)
    )
def _cross(a: pl.Series, b: pl.Series) -> pl.Series:
    """CROSS(A,B)：A 当根 > B 且 前根 A ≤ B。"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def compute_wash_lines(df: pl.DataFrame, n1: int = 3, n2: int = 21) -> dict[str, pl.Series]:
    """返回 8 个序列：四条强弱线 + 四个买入信号柱（命中 = −30，否则 0）。"""
    close = df["close"]
    low = df["low"]

    short = _rsv(close, low, close, n1)
    mid = _rsv(close, low, close, 10)
    mid_long = _rsv(close, low, close, 20)
    long_ = _rsv(close, low, close, n2)

    out = df.select(
        short.alias("xpsd_short"),
        mid.alias("xpsd_mid"),
        mid_long.alias("xpsd_midlong"),
        long_.alias("xpsd_long"),
        pl.when((short <= 6) & (mid <= 6) & (mid_long <= 6) & (long_ <= 6))
        .then(-30.0)
        .otherwise(0.0)
        .alias("xpsig_zero"),
        pl.when((short <= 20) & (long_ >= 60))
        .then(-30.0)
        .otherwise(0.0)
        .alias("xpsig_w20"),
        pl.when(_cross(short, long_) & (long_ < 20))
        .then(-30.0)
        .otherwise(0.0)
        .alias("xpsig_xlong"),
        pl.when(_cross(short, mid) & (mid < 30))
        .then(-30.0)
        .otherwise(0.0)
        .alias("xpsig_xmid"),
    )
    return {name: out[name] for name in out.columns}
