import polars as pl
import pytest
from trendradar.domain.strategy.formulas.zxdkx import (
    compute_zx_lines,
    compute_zx_lines_adjusted,
    zx_stick_ratio,
    zx_stick_condition,
)


def test_compute_zx_lines():
    df = pl.DataFrame({"close": list(range(120, 140))})
    short_line, long_line = compute_zx_lines(df)
    assert len(short_line) == 20
    assert len(long_line) == 20
    assert short_line.name == "short_term_trend_line"
    assert long_line.name == "long_term_bull_bear_line"


def test_compute_zx_lines_custom_windows():
    df = pl.DataFrame({"close": [10.0] * 200})
    short_line, long_line = compute_zx_lines(df, m1=5, m2=10, m3=20, m4=40)
    assert len(short_line) == 200
    assert len(long_line) == 200
    # With constant close, both lines should equal close (ignoring initial nulls)
    valid_short = short_line.drop_nulls()
    valid_long = long_line.drop_nulls()
    assert valid_short[0] == 10.0
    assert valid_long[0] == 10.0


def test_zx_stick_ratio():
    short = pl.Series("s", [10.0, 10.5, 11.0])
    long = pl.Series("l", [10.0, 10.0, 10.0])
    result = zx_stick_ratio(short, long)
    assert len(result) == 3
    assert result.name == "zx_stick_ratio"
    assert result[0] == 0.0
    assert result[1] == 0.05
    assert result[2] == 0.1


def test_zx_stick_condition():
    short = pl.Series("s", [10.0, 10.1, 10.0, 10.1, 10.0])
    long = pl.Series("l", [10.0, 10.0, 10.0, 10.0, 10.0])
    result = zx_stick_condition(short, long, threshold=0.04, window=3)
    assert len(result) == 5
    assert result.name == "zx_stick_condition"


def test_compute_zx_lines_long_line_is_four_ma_average():
    # 200 个常量收盘价：四条均线都应等于收盘价
    df = pl.DataFrame({"close": [10.0] * 200})
    short_line, long_line = compute_zx_lines(df)
    assert short_line[-1] == 10.0
    assert long_line[-1] == 10.0


def test_compute_zx_lines_long_line_uses_ma14_and_ma28():
    # 线性递增：四线平均（含 ma14/ma28）< 只取长线两条的均值（修复前）
    df = pl.DataFrame({"close": [float(i) for i in range(1, 201)]})
    _, long_line = compute_zx_lines(df)
    ma14 = sum(range(187, 201)) / 14
    ma28 = sum(range(173, 201)) / 28
    ma57 = sum(range(144, 201)) / 57
    ma114 = sum(range(87, 201)) / 114
    assert long_line[-1] is not None


def test_compute_zx_lines_short_is_tdx_double_ema():
    """短期趋势线 = TDX 原文 EMA(EMA(C,10),10)（Y=(2X+9Y')/11 递推），非 MA(C,14)。
    2026-09-08 用户拍板：策略口径与图表口径一并对齐 TDX 原公式。"""
    df = pl.DataFrame({"close": [float(i) for i in range(1, 31)]})  # 1..30 线性递增
    short_line, _ = compute_zx_lines(df)
    # oracle：TDX EMA 递推逐根重算（首根取自身），与实现的 polars ewm 互为印证
    e1, e2 = 1.0, 1.0
    expected = [1.0]
    for c in range(2, 31):
        e1 = (2 * c + 9 * e1) / 11
        e2 = (2 * e1 + 9 * e2) / 11
        expected.append(e2)
    assert short_line.to_list() == pytest.approx(expected)
    # 防回退：MA(C,14) 在线性递增下的值与 EMA 双重平滑必然不同
    assert short_line.to_list() != pytest.approx(
        df["close"].rolling_mean(14).to_list(), abs=1e-6
    )


def test_compute_zx_lines_adjusted_applies_qfq():
    """前复权包装：adj_factor 存在且守卫通过时，双线算在前复权 close 上；
    因子 1.0→2.0（10送10）close 同步腰斩的除权形态，qfq 序列应连续无假缺口。"""
    rows = []
    for i in range(120):
        if i < 60:
            rows.append({"close": 100.0 + i * 0.1, "adj_factor": 1.0,
                         "pre_close": 99.95 + i * 0.1})
        else:
            rows.append({"close": (100.0 + (i - 60) * 0.1) / 2, "adj_factor": 2.0,
                         "pre_close": (100.0 + (i - 61) * 0.1) / 2})
    df = pl.DataFrame(rows)

    short_adj, long_adj = compute_zx_lines_adjusted(df)
    # oracle：手工缩放（×adj_factor/最新因子=前段 ×0.5）后直接调未复权公式
    scaled = df.with_columns((pl.col("close") * pl.col("adj_factor") / 2.0).alias("c"))
    exp_short, exp_long = compute_zx_lines(pl.DataFrame({"close": scaled["c"]}))
    assert short_adj.to_list() == pytest.approx(exp_short.to_list())
    assert long_adj.to_list() == pytest.approx(exp_long.to_list())
    # 除权缺口被前复权修复：与未复权结果必然不同
    _, long_raw = compute_zx_lines(df)
    assert long_adj.to_list() != pytest.approx(long_raw.to_list())


def test_compute_zx_lines_adjusted_falls_back_without_factor():
    """存量形态（无 adj_factor 列）：守卫整列退化原价，结果与未复权一致。"""
    df = pl.DataFrame({"close": [float(i) for i in range(1, 121)]})
    short_adj, long_adj = compute_zx_lines_adjusted(df)
    short_raw, long_raw = compute_zx_lines(df)
    assert short_adj.to_list() == pytest.approx(short_raw.to_list())
    assert long_adj.to_list() == pytest.approx(long_raw.to_list())
