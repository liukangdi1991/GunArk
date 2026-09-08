"""个股K线 domain 测试：聚合不变量、qfq 守卫/缩放、编排判别式断言（spec §7）。"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from trendradar.domain.market.adjust import apply_qfq
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    aggregate_bars,
    build_kline_series,
)


def _daily(dates, closes, factors, pre_close="real"):
    """构造日线 fixture。pre_close="real" 全真值（已重建形态）；None 无该列（存量形态）；
    list 逐行给定（增量合并形态，可含 null）。"""
    rows = []
    for i, (d, c, f) in enumerate(zip(dates, closes, factors)):
        rows.append({
            "code": "000001",
            "date": d,
            "open": c - 0.1,
            "high": c + 0.2,
            "low": c - 0.3,
            "close": c,
            "volume": 100.0 + i,
            "amount": 1000.0 + i,
            "adj_factor": f,
            "is_suspended": False,
        })
    if pre_close == "real":
        for r in rows:
            r["pre_close"] = r["close"] - 0.05
    elif pre_close is not None:
        for r, pc in zip(rows, pre_close):
            r["pre_close"] = pc
    return pl.DataFrame(rows)


def test_weekly_cross_year_stays_one_bar():
    # 2025-12-29(一)/12-31/2026-01-02 同一自然周——禁止 (year, week) 劈分（M16）
    df = _daily(
        dates=[date(2025, 12, 29), date(2025, 12, 31), date(2026, 1, 2)],
        closes=[10.0, 11.0, 12.0],
        factors=[1.0, 1.0, 1.0],
    )
    weekly = aggregate_bars(df, "weekly")
    assert weekly.height == 1
    row = weekly.row(0, named=True)
    assert row["date"] == date(2026, 1, 2)          # 组内最后交易日
    assert row["open"] == pytest.approx(9.9)        # 首日 open
    assert row["high"] == pytest.approx(12.2)       # max high
    assert row["low"] == pytest.approx(9.7)         # min low
    assert row["close"] == pytest.approx(12.0)      # 末日 close
    assert row["pre_close"] == pytest.approx(9.95)  # 组内首日 pre_close（可比昨收）
    assert row["volume"] == pytest.approx(303.0)
    assert row["amount"] == pytest.approx(3003.0)


def test_monthly_and_volume_conservation():
    df = _daily(
        dates=[date(2025, 1, 2), date(2025, 1, 31), date(2025, 2, 5)],
        closes=[10.0, 10.5, 11.0],
        factors=[1.0, 1.0, 1.0],
    )
    monthly = aggregate_bars(df, "monthly")
    assert monthly.height == 2
    assert monthly["date"].to_list() == [date(2025, 1, 31), date(2025, 2, 5)]
    assert monthly["volume"].sum() == pytest.approx(df["volume"].sum())  # 守恒
    assert monthly["close"].to_list() == pytest.approx([10.5, 11.0])


# ---------- qfq 守卫与缩放（spec §4.3.1 / §4.4 / R2/R7） ----------


def test_qfq_scales_by_latest_factor_literal_oracle():
    # R1 oracle：除权形态物理自洽（因子 1.2→1.5 时 close 同步 100.5→80.4，
    # pre_close 遵循恒等式 pre_t ≈ close_{t−1}×f_{t−1}/f_t）→ 守卫全过；
    # 钉死「分母 = 最新因子」：前段 ×1.2/1.5、末段 ×1
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(4)],
        closes=[100.0, 100.5, 80.5, 80.8],
        factors=[1.2, 1.2, 1.5, 1.5],
        pre_close=[99.95, 100.45, 80.4, 80.6],
    )
    out, degraded = apply_qfq(df)
    assert degraded is False
    assert out["close"].to_list() == pytest.approx(
        [100.0 * 1.2 / 1.5, 100.5 * 1.2 / 1.5, 80.5, 80.8]
    )
    assert out["pre_close"].to_list()[0] == pytest.approx(99.95 * 1.2 / 1.5)  # R3


def test_qfq_pre_close_scaled_with_bar():
    # R3：qfq 档 pre_close 随同 bar 缩放，行内量纲一致
    df = _daily(
        dates=[date(2025, 3, 3), date(2025, 3, 4)],
        closes=[10.0, 10.1],
        factors=[1.2, 1.2],
    )
    out, _ = apply_qfq(df)
    assert out["pre_close"].to_list() == pytest.approx([9.95 * 1.2 / 1.2, 10.05 * 1.2 / 1.2])


def test_qfq_degrades_on_null_factor():
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.2, None])
    out, degraded = apply_qfq(df)
    assert degraded is True
    assert out["close"].to_list() == [10.0, 10.1]  # 整列退化，禁止部分缩放


def test_qfq_degrades_on_zero_and_nan_factor():
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.2, 0.0])
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0, 10.1]
    df2 = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                 factors=[1.2, float("nan")])  # M1：NaN 的 null_count()==0，须显式查
    out2, degraded2 = apply_qfq(df2)
    assert degraded2 is True and out2["close"].to_list() == [10.0, 10.1]


def test_qfq_degrades_when_identity_breaks():
    # 因子 4× 跳变但 pre_close 未同步调整（占位/污染形态）→ 恒等式破 → degraded
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 4.0])
    out, degraded = apply_qfq(df)
    assert degraded is True
    assert out["close"].to_list() == [10.0, 10.1]


def test_qfq_passes_rights_issue_with_consistent_pre_close():
    # 配股形态（002747 实测同类）：相邻因子比 4×>3×，但 pre_close 遵循恒等式
    # （25×4 = 100×1）→ 因子真实，正常缩放不误降级（越带守卫仅限未重建数据）
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[100.0, 26.0],
                factors=[1.0, 4.0], pre_close=[99.0, 25.0])
    out, degraded = apply_qfq(df)
    assert degraded is False
    assert out["close"].to_list() == pytest.approx([25.0, 26.0])


def test_missing_factor_column_degrades():
    df = _daily(dates=[date(2025, 3, 3)], closes=[10.0], factors=[1.0]).drop("adj_factor")
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0]


def test_legacy_const_one_degrades_only_when_not_rebuilt():
    # 未重建（无 pre_close 列）：恒 1.0 → degraded（B1 故障态）
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 1.0], pre_close=None)
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0, 10.1]
    # 已重建（pre_close 真值）：恒 1.0 是合法形态（上市从未除权的新股，R2）
    df2 = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                 factors=[1.0, 1.0])
    out2, degraded2 = apply_qfq(df2)
    assert degraded2 is False and out2["close"].to_list() == [10.0, 10.1]


def test_incremental_merged_legacy_file_still_degrades():
    # R7 穿透用例：旧文件 + _align_columns 式增量合并（旧行 pre_close=null、新行真因子）
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(5)],
        closes=[10.0, 10.05, 10.1, 10.2, 10.3],
        factors=[1.0, 1.0, 1.0, 2.0, 2.0],
        pre_close=[None, None, None, 10.15, 10.25],  # null_count=3 > 1 → 判未重建
    )
    out, degraded = apply_qfq(df)
    assert degraded is True  # 混合守卫仍触发，F ≤ 3 不漏
    assert out["close"].to_list() == [10.0, 10.05, 10.1, 10.2, 10.3]


def test_rebuilt_legit_split_stock_scales():
    # 已重建 + 合法除权形态（10送10：因子倍增、close 腰斩、pre_close 遵循恒等式）
    # → 正常缩放不误降级；qfq 序列连续无假缺口
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(5)],
        closes=[10.00, 10.05, 10.10, 5.05, 5.10],
        factors=[1.0, 1.0, 1.0, 2.0, 2.0],
        pre_close=[9.95, 10.00, 10.00, 5.05, 5.05],
    )
    out, degraded = apply_qfq(df)
    assert degraded is False
    assert out["close"].to_list() == pytest.approx([5.0, 5.025, 5.05, 5.05, 5.10])


# ---------- 编排（spec §4.3 / M5 判别式断言） ----------


def test_build_qfq_vs_none_discriminates_and_last_bar_equal():
    # 物理自洽 fixture：因子 1.2→1.5 时 close 10.10→8.16、pre_close 遵循恒等式
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(4)],
        closes=[10.00, 10.05, 10.10, 8.16],
        factors=[1.2, 1.2, 1.2, 1.5],
        pre_close=[9.95, 10.00, 10.05, 8.08],
    )
    qfq_bars, qfq_degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.QFQ)
    none_bars, none_degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.NONE)
    assert qfq_degraded is False and none_degraded is False
    assert qfq_bars["close"].to_list() != none_bars["close"].to_list()
    assert qfq_bars["close"][-1] == pytest.approx(none_bars["close"][-1])  # 末根 scale=1
    assert qfq_bars["close"].to_list() == [8.0, 8.04, 8.08, 8.16]  # round(4) 后逐值


def test_build_adjust_precedes_aggregate_literal_oracle():
    # 除权步 1.2→1.5 落在同一自然周（物理自洽：close 100.5→80.5、pre_close 遵循恒等式）
    df = _daily(
        dates=[date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6)],
        closes=[100.0, 100.5, 80.5, 80.8],
        factors=[1.2, 1.2, 1.5, 1.5],
        pre_close=[99.95, 100.45, 80.4, 80.6],
    )
    weekly, degraded = build_kline_series(df, KlinePeriod.WEEKLY, AdjustMode.QFQ)
    assert degraded is False
    assert weekly.height == 1
    row = weekly.row(0, named=True)
    assert row["close"] == pytest.approx(80.8)
    assert row["open"] == pytest.approx(99.9 * 1.2 / 1.5)


def test_build_zx_short_is_tdx_double_ema_and_long_is_ma_composite():
    # 短期趋势线 = 用户 TDX 原文公式 EMA(EMA(C,10),10)（Y=(2X+9Y')/11 递推）——
    # 与策略侧 short_term_trend_line(MA14) 有意不同（spec §4.4 已知不一致清单）；
    # 多空线 = MA(14/28/57/114) 均值组合，窗口不足为 null。
    closes = [10.0 + 0.1 * i for i in range(20)]
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(20)],
        closes=closes,
        factors=[1.0] * 20,
    )
    bars, degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.QFQ)
    assert degraded is False

    # oracle：按 TDX 递推公式逐根独立重算（与实现的 polars ewm 互为印证）
    e1 = closes[0]
    e2 = closes[0]
    expected_short: list[float] = [closes[0]]
    for c in closes[1:]:
        e1 = (2 * c + 9 * e1) / 11
        e2 = (2 * e1 + 9 * e2) / 11
        expected_short.append(e2)
    assert bars["zx_short"].to_list() == pytest.approx(expected_short, abs=1e-4)  # round(4) 契约
    assert bars["zx_long"].null_count() == 20  # MA114 窗口不足，组合恒 null
    assert bars["zx_short"].to_list() != bars["zx_long"].to_list()  # 防两线交换


def test_build_tolerates_missing_pre_close_column():
    # N3：存量文件无 pre_close 列（none 档）也要能出序列
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 1.0], pre_close=None)
    bars, _ = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.NONE)
    assert bars["pre_close"].null_count() == 2  # 容错补 null 列
    assert bars["close"].to_list() == [10.0, 10.1]


def test_build_empty_raises_bars_unavailable():
    df = _daily(dates=[date(2025, 3, 3)], closes=[10.0], factors=[1.0])
    with pytest.raises(BarsUnavailable):
        build_kline_series(df.head(0), KlinePeriod.DAILY, AdjustMode.QFQ)
