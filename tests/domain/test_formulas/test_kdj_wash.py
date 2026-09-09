"""知行洗盘线公式测试（用户 TDX 原文，2026-09-08）。"""
from datetime import date, timedelta

import polars as pl
import pytest

from trendradar.domain.strategy.formulas.kdj_wash import compute_wash_lines


def _mid_position_df(n: int = 30) -> pl.DataFrame:
    """两阶段形态：前 15 根收在 10.5（=高），后 15 根收在 10.0（=低）。
    尾部各窗口：HHV(C,N)=10.5、LLV(L,N)=9.5 → RSV = (10−9.5)/(10.5−9.5)×100 = 50。"""
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    rows = []
    for i in range(n):
        if i < n // 2:
            rows.append({"date": dates[i], "close": 10.5, "low": 10.0, "high": 10.5})
        else:
            rows.append({"date": dates[i], "close": 10.0, "low": 9.5, "high": 10.5})
    return pl.DataFrame(rows)


def test_two_phase_fixture_pinned_sequences():
    # 两阶段形态（前 15 根收在窗口高、后 15 根收在窗口低）：窗口滚动使各线
    # 在不同时点切换 100/50——按实现输出钉死序列（TDX 对齐回归基线）
    bars = compute_wash_lines(_mid_position_df())
    assert bars["xpsd_short"].to_list() == [100.0] * 15 + [50.0] * 2 + [100.0] * 13
    assert bars["xpsd_mid"].to_list() == [100.0] * 15 + [50.0] * 9 + [100.0] * 6
    assert bars["xpsd_midlong"].to_list() == [100.0] * 15 + [50.0] * 15
    assert bars["xpsd_long"].to_list() == [100.0] * 15 + [50.0] * 15
    for name in ("xpsig_zero", "xpsig_w20", "xpsig_xlong", "xpsig_xmid"):
        assert bars[name].to_list() == [0.0] * 30  # RSV ∈ {50,100}：无超卖、无金叉


def test_all_oversold_fires_zero_signal():
    # 持续下跌且 close == low（每根都是窗口新低）→ 四线 RSV 全为 0 → 四线归零买触发
    n = 30
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pl.DataFrame({
        "date": dates,
        "close": [30.0 - i for i in range(n)],
        "low": [30.0 - i for i in range(n)],      # close == low
        "high": [31.0 - i for i in range(n)],
    })
    bars = compute_wash_lines(df)
    assert bars["xpsd_short"].to_list() == [0.0] * n    # RSV = 0（每根都是窗口最低）
    assert bars["xpsig_zero"].to_list() == [-30.0] * n  # 首根即满足：四线全 0 ≤ 6
    assert bars["xpsig_w20"].to_list() == [0.0] * n     # 长期 = 0 < 60 → 不触发


def test_long_line_has_no_null_warmup():
    # TDX 收缩窗口：n2=21 的长期线自首根即有值（尾部 RSV=50 形态）
    bars = compute_wash_lines(_mid_position_df(30))
    assert bars["xpsd_long"].null_count() == 0
    assert bars["xpsd_long"][20] == pytest.approx(50.0)  # 尾部形态
