"""个股 K 线序列领域计算：枚举、异常、聚合、编排。纯 polars，无 IO。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import polars as pl

from trendradar.domain.market.adjust import apply_qfq
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class KlinePeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AdjustMode(str, Enum):
    QFQ = "qfq"
    NONE = "none"


class BarsUnavailable(RuntimeError):
    """bars 文件缺失或 0 行 → route 显式 404。"""


class MarketDataUnavailable(RuntimeError):
    """bars 目录缺失或读 IO 异常 → route 显式 503。"""


@dataclass(frozen=True)
class KlineSeries:
    """service 组装的域对象（F1/F6/G1）：degraded 与 meta 结果走独立通道。"""

    bars: pl.DataFrame
    adjust_degraded: bool
    name: str
    industry: str | None


def aggregate_bars(df: pl.DataFrame, period: str) -> pl.DataFrame:
    """自然周（周一锚）/自然月聚合；组内无 bar 不产 bar（停牌整周/月，N3）。"""
    if period == "weekly":
        key = pl.col("date").dt.truncate("1w")
    else:
        key = pl.col("date").dt.truncate("1mo")
    return (
        df.sort("date")
        .group_by(key.alias("_k"))
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("pre_close").first(),
            pl.col("volume").sum(),
            pl.col("amount").sum(),
            pl.col("date").last().alias("date"),
        )
        .drop("_k")
        .sort("date")
    )


def _ensure_optional_columns(df: pl.DataFrame) -> pl.DataFrame:
    """列访问容错（N3）：存量文件可能缺 pre_close 列（空帧 schema 才有）。"""
    if "pre_close" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("pre_close"))
    return df


def build_kline_series(
    df: pl.DataFrame, period: KlinePeriod, adjust: AdjustMode
) -> tuple[pl.DataFrame, bool]:
    """编排：sort → 0 行短路 → qfq → 聚合 → 附 zx 两线 → round(4)。

    返回 (bars, adjust_degraded)。KlineSeries 由 service 独家构造（G1）：
    domain 无 meta，不造 name/industry 占位值。
    """
    if df.is_empty():
        raise BarsUnavailable("无该股行情数据")
    df = _ensure_optional_columns(df).sort("date")
    degraded = False
    if adjust == AdjustMode.QFQ:
        df, degraded = apply_qfq(df)
    if period == KlinePeriod.DAILY:
        out = df
    else:
        out = aggregate_bars(df, period.value)
    # 多空线：复用策略公式 compute_zx_lines（窗口 14/28/57/114 的 MA 均值，与选股同源）
    _, long_ = compute_zx_lines(out)
    # 短期趋势线：用户 TDX 原文公式 EMA(EMA(C,10),10)（Y=(2X+9Y')/11 ⟺ alpha=2/11）——
    # 与策略侧 short_term_trend_line(MA14) 有意不同（spec §4.4 已知不一致清单）
    short = (
        out["close"]
        .ewm_mean(alpha=2 / 11, adjust=False)
        .ewm_mean(alpha=2 / 11, adjust=False)
    )
    out = out.with_columns(
        short.alias("zx_short"),
        long_.alias("zx_long"),
    )
    out = out.with_columns(
        pl.col(c).round(4)
        for c in ("open", "high", "low", "close", "pre_close", "zx_short", "zx_long")
        if c in out.columns
    )
    out = out.select(
        "date", "open", "high", "low", "close", "pre_close",
        "volume", "amount", "zx_short", "zx_long",
    )
    return out, degraded
