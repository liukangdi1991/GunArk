"""砖形图 MT 色柱派生测试（用户 TDX 原文 STICKLINE 着色规则，2026-09-08）。"""
import polars as pl
import pytest

from trendradar.domain.strategy.formulas.mt_oscillator import compute_mt, compute_mt_brick


def _frame_from_close(closes: list[float]) -> pl.DataFrame:
    """中位位置形态（high = close+0.2、low = close−0.2）→ 稳态 MT ≈ 86。"""
    n = len(closes)
    dates = [None] * n  # compute_mt 只用 high/low/close
    return pl.DataFrame({
        "close": closes,
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
    })


def test_steady_state_flat_yields_null_colors():
    # 平盘：MT 持平 → 红/绿/橙全 null（MT 持平无色）
    df = _frame_from_close([10.0] * 20)
    fields = compute_mt_brick(df["high"], df["low"], df["close"])
    assert fields["mt"][-1] > 0  # clip 后稳态正值
    for name in ("mt_red", "mt_green", "mt_orange"):
        assert fields[name][-1] is None  # 持平：无着色（末根与前根相等）


def test_rise_fall_rise_yields_expected_colors():
    # 收盘阶梯：中位平台(86) → 冲顶平台(MT↑ 红) → 回落平台(MT↓ 绿) → 再冲顶(橙候选)
    closes = (
        [10.0] * 12           # 中位稳态：MT ≈ 86
        + [12.0] * 12         # 冲顶：MT 上升（红）后持平
        + [6.0] * 12          # 回落：MT 下降（绿）
        + [10.0] * 12         # 再回升：MT 上升（橙候选：回升且力度对比）
    )
    df = _frame_from_close(closes)
    fields = compute_mt_brick(df["high"], df["low"], df["close"])
    mt = fields["mt"]
    # 形态健全性：三段各自稳态值单调
    assert mt[10] == pytest.approx(mt[11])          # 平台内持平
    # 冲顶段尾部出现红柱（上行）
    red_idx = [i for i, v in enumerate(fields["mt_red"].to_list()) if v is not None]
    assert red_idx, "上行段应有红柱"
    green_idx = [i for i, v in enumerate(fields["mt_green"].to_list()) if v is not None]
    assert green_idx, "回落段应有绿柱"
    # 红/绿互斥：任一根不得同时着红与绿
    for i in range(len(closes)):
        assert not (fields["mt_red"][i] is not None and fields["mt_green"][i] is not None)
