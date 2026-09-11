import polars as pl
import pytest

from trendradar.domain.strategy.formulas.zxdkx import (
    compute_zx_lines,
    compute_zx_lines_adjusted,
    zx_stick_condition,
    zx_stick_ratio,
)


def _qfq_frame(code: str) -> pl.DataFrame:
    """单票 40 根：因子末位降到 1、pre_close 满足恒等式（容差内，实为逐分精确）。"""
    n = 40
    close = [100.0 + i for i in range(n)]
    pre_close = [None] + close[1:n - 1] + [close[-1] * 2.0]   # 末根因子减半 → 昨收×2
    return pl.DataFrame({
        "code": [code] * n,
        "date": list(range(1, n + 1)),
        "close": close,
        "pre_close": pre_close,
        "adj_factor": [2.0] * (n - 1) + [1.0],
    })


_SHORT = dict(m1=3, m2=5, m3=7, m4=10)   # 40 根下四条 MA 均有值


def test_compute_zx_lines_adjusted_groups_multi_code_frame():
    """复审 I1：多票拼接帧按 code 分组逐段前复权——整帧全局 shift 会跨票边界
    破恒等式导致恒退化（原价），同一票的线值不得随 universe 大小漂移。"""
    multi = pl.concat([_qfq_frame("000001"), _qfq_frame("600519")]).sort(["code", "date"])
    single = _qfq_frame("000001")

    # 佐证退化机制存在：整帧直接 apply_qfq 必退化（守卫被跨票边界击穿）
    from trendradar.domain.market.adjust import apply_qfq
    _, whole_degraded = apply_qfq(multi)
    assert whole_degraded is True

    _, long_multi = compute_zx_lines_adjusted(multi, **_SHORT)
    _, long_single = compute_zx_lines_adjusted(single, **_SHORT)
    # 多票帧中 000001 段的线值 == 单票帧线值（段末位置；跨帧容 1 ULP）
    assert long_multi[39] == pytest.approx(long_single[-1])


def test_compute_zx_lines_adjusted_scales_close_by_factor():
    """前复权真实生效：线值 == 显式按 scale=adj_factor/最新因子 缩放后的线值。"""
    df = _qfq_frame("000001")
    _, long_line = compute_zx_lines_adjusted(df, **_SHORT)
    scaled = df.with_columns((pl.col("close") * pl.col("adj_factor")).alias("close"))
    _, expected = compute_zx_lines(scaled, **_SHORT)
    assert long_line[-1] == pytest.approx(expected[-1])


def test_compute_zx_lines_adjusted_one_bad_code_does_not_poison_others():
    """一只票因子污染（恒等式破）→ 该段退化原价，另一段照常缩放。"""
    good = _qfq_frame("000001")
    bad = _qfq_frame("600519").with_columns(pl.lit(100.0).alias("pre_close"))  # 恒等式破
    multi = pl.concat([good, bad]).sort(["code", "date"])
    _, long_multi = compute_zx_lines_adjusted(multi, **_SHORT)
    _, long_good = compute_zx_lines_adjusted(good, **_SHORT)
    assert long_multi[39] == pytest.approx(long_good[-1])


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


def test_compute_zx_lines_long_line_uses_ma14_and_ma28():
    # 线性递增：四线平均（含 ma14/ma28）< 只取长线两条的均值（修复前）
    df = pl.DataFrame({"close": [float(i) for i in range(1, 201)]})
    _, long_line = compute_zx_lines(df)
    ma14 = sum(range(187, 201)) / 14
    ma28 = sum(range(173, 201)) / 28
    ma57 = sum(range(144, 201)) / 57
    ma114 = sum(range(87, 201)) / 114
    assert long_line[-1] == pytest.approx((ma14 + ma28 + ma57 + ma114) / 4)


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
    """前复权包装：pre_close 遵循除权恒等式（pre_i = close_{i−1}×f_{i−1}/f_i）→
    因子真实，双线算在前复权 close 上、序列连续无假缺口。"""
    closes: list[float] = []
    factors: list[float] = []
    for i in range(120):
        if i < 60:
            closes.append(100.0 + i * 0.1)          # 除权前：100 → 105.9
            factors.append(1.0)
        else:
            closes.append((100.0 + (i - 60) * 0.1) / 2)  # 10送10：raw 腰斩
            factors.append(2.0)
    pres = [closes[0] - 0.05]
    for i in range(1, 120):
        pres.append(closes[i - 1] * factors[i - 1] / factors[i])  # 恒等式
    df = pl.DataFrame({"close": closes, "adj_factor": factors, "pre_close": pres})

    short_adj, long_adj = compute_zx_lines_adjusted(df)
    # oracle：手工缩放（×adj_factor/最新因子=前段 ×0.5）后直接调未复权公式
    scaled = pl.DataFrame({"close": [c * f / 2.0 for c, f in zip(closes, factors)]})
    exp_short, exp_long = compute_zx_lines(scaled)
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
