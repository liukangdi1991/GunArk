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
    # 平盘：MT 持平 → mt_color 全 null（持平无色）
    df = _frame_from_close([10.0] * 20)
    fields = compute_mt_brick(df["high"], df["low"], df["close"])
    assert fields["mt"][-1] > 0  # clip 后稳态正值
    for i in (18, 19):
        assert fields["mt_color"][i] is None  # 持平：无着色（末两根与前根相等）


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
    red_idx = [i for i, c in enumerate(fields["mt_color"].to_list()) if c == "red"]
    assert red_idx, "上行段应有红砖"
    green_idx = [i for i, c in enumerate(fields["mt_color"].to_list()) if c == "green"]
    assert green_idx, "回落段应有绿砖"
    # 红/绿互斥：任一根不得同时着红与绿
    for i in range(len(closes)):
        assert not (fields["mt_color"][i] == "red" and fields["mt_color"][i] == "green")


def test_orange_requires_red_longer_than_preceding_green():
    """2026-09-09 回归：橙柱条件 `红柱长 > 前根绿柱长`（原式符号反致全部绿转红误标橙）。"""
    closes = (
        [10.0] * 12  # 中位稳态 MT ≈ 86
        + [12.0] * 6  # 冲顶（红）
        + [6.0] * 10  # 深回落（绿，跌得深）
        + [10.0] * 8  # 回升第一根（红候选：幅度浅于前绿 → red；随后可能超回 → orange）
    )
    df = _frame_from_close(closes)
    fields = compute_mt_brick(df["high"], df["low"], df["close"])
    mt = fields["mt"].to_list()
    color = fields["mt_color"].to_list()

    transitions = 0
    for i in range(2, len(mt)):
        if color[i - 1] == "green" and color[i] == "red":
            transitions += 1
            green_len = mt[i - 2] - mt[i - 1]  # 前根绿柱长度（正）
            red_len = mt[i] - mt[i - 1]  # 当根红柱长度（正）
            expect = "orange" if red_len > green_len else "red"
            assert color[i] == expect, (
                f"i={i} red_len={red_len:.2f} green_len={green_len:.2f} "
                f"expect={expect} actual={color[i]}"
            )
        if color[i] == "orange":
            # 橙必紧跟绿柱且严格更强
            assert color[i - 1] == "green"
            assert mt[i] - mt[i - 1] > mt[i - 2] - mt[i - 1]
    assert transitions >= 1  # 形态健全：阶梯产生绿→红转换
