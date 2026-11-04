"""放量回踩（趋势线/多空线）选择行为验证。

语义（用户 2026-09-18 确认）：流通市值≥50亿 + 短期线>多空线 + 20日内存在倍量柱
+ |收盘−目标线|/目标线 < 2%（贴线）+ 收盘≥目标线。
目标线：放量回踩趋势线=短期线（DUAN_QI=EMA(EMA(C,10),10)）；放量回踩多空线=多空线
（4-MA 14/28/57/114）。与 B1 共用公式层，指标同用前复权价。

测试数据构造：先涨后平的序列让 DUAN≈收盘、DUO 略低（上涨段混入均线）；
用 compute_b1_columns 实测 DUAN/DUO 后再定位最后一根收盘，保证「贴线/远离」
判据确定性成立。

另覆盖（边界与口径）：
- 除权（adj_factor）前复权贴线：raw×adj/最新adj 复原纯 qfq 序列，无 adj 时
  多空线混入除权前高价 → 结构反转不选（原 bug 形态）
- 倍量柱 20 日窗口边界：倒数第 20 根（窗口内）/第 21 根（窗口外）
- 贴线阈值带：偏离 1.8% 选中、2.2% 不选（恰好 2.0% 处于浮点不可判定区，不测）
- 贴线但收盘略低于目标线：贴线满足仍被「收在线上」排除
"""

from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.formulas.b1 import compute_b1_columns
from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import get_defn, make_empty_df

_D0 = date(2026, 1, 1)


def _make_context(df, market_cap=None):
    return SelectionContext(
        trade_date=df["date"].max(),
        market_data=df,
        candidate_codes=df["code"].unique().to_list(),
        market_cap=market_cap,
    )


def _run(strategy_id, df, market_cap=None):
    defn = get_defn(strategy_id)
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    return sel.select_day(_make_context(df, market_cap), warmup).selected_codes


def _closes(n=130, rise_bars=100, base=8.5, top=10.0, last_close=None):
    """先线性上涨 rise_bars 根到 top，再平盘的收盘序列。"""
    closes = [base + (top - base) * i / max(rise_bars - 1, 1) if i < rise_bars else top
              for i in range(n)]
    if last_close is not None:
        closes[-1] = last_close
    return closes


def _df_from_closes(closes, code="A", volumes=None, adj_factor=None):
    """closes → 单票 OHLC df；volume 可逐根覆盖；adj_factor 可选（前复权用例）。"""
    rows = []
    for i, c in enumerate(closes):
        row = {
            "code": code, "date": _D0 + timedelta(days=i),
            "open": c, "high": c + 0.05, "low": c - 0.05, "close": c,
            "volume": volumes[i] if volumes else 1_000_000.0,
        }
        if adj_factor is not None:
            row["adj_factor"] = adj_factor[i]
        rows.append(row)
    return pl.DataFrame(rows)


def _bars(code: str, n=130, rise_bars=100, base=8.5, top=10.0, volumes=None, last_close=None):
    """先线性上涨 rise_bars 根到 top，再平盘；volume 可逐根覆盖。"""
    return _df_from_closes(
        _closes(n, rise_bars, base, top, last_close), code=code, volumes=volumes
    )


def _spiked_volumes(n=130, idx=-8, multiple=3.0):
    """常数成交量 + 指定位置一根倍量柱（idx 为 Python 负下标，-1=当日）。"""
    v = [1_000_000.0] * n
    v[idx] = v[idx] * multiple
    return v


def _duan_duo(df):
    out = compute_b1_columns(df)
    latest = out.row(-1, named=True)
    return latest["_b1_duan_qi"], latest["_b1_duo_kong"]


def _with_volume_spike(df):
    v = df["volume"].to_list()
    v[-8] = v[-8] * 3.0  # 20 日内一根倍量柱
    return df.with_columns(pl.Series("volume", v))


def _solve_last_close(base_closes, line_idx, dev):
    """迭代求 last_close，使最终 df 上 |close−目标线|/目标线 ≈ dev。

    目标线随 last_close 微移（对收盘的敏感度 ~3%），定点迭代 10 次残差 <1e-6——
    用于把贴线判据推到阈值附近的确定性测试点。line_idx: 0=DUAN_QI 1=DUO_KONG。
    """
    closes = list(base_closes)
    closes[-1] = closes[-2]
    for _ in range(10):
        line = _duan_duo(_df_from_closes(closes))[line_idx]
        closes[-1] = line * (1 + dev)
    return closes


def test_both_select_when_close_hugs_lines():
    """平盘尾段：DUAN≈DUO≈收盘（偏差<2%），两策略都选出。"""
    df = _with_volume_spike(_bars("A"))
    code_cap = {"A": 60 * 10000.0}  # 60 亿（万元口径）

    assert _run("volume_pullback_trend", df, code_cap) == ["A"]
    assert _run("volume_pullback_bullbear", df, code_cap) == ["A"]


def test_trend_only_when_close_hugs_duan_but_far_above_duo():
    """陡涨后短平盘（15 根）：DUO 被上涨段拖低 ≥3%，收盘只贴 DUO → 仅多空线版选出。"""
    df = _with_volume_spike(_bars("A", n=115, rise_bars=100, base=5.0, top=10.0))
    duan, duo = _duan_duo(df)
    assert duan - duo > 0.03 * duo, f"测试数据构造失败：DUAN/DUO 价差不足 ({duan:.3f}/{duo:.3f})"
    hug_duo_close = duo * 1.015  # 贴 DUO（<2%）但低于 DUAN（收盘 < DUAN 直接不满足趋势线版）
    df2 = _with_volume_spike(_bars("A", n=115, rise_bars=100, base=5.0, top=10.0, last_close=hug_duo_close))

    assert _run("volume_pullback_bullbear", df2, {"A": 60 * 10000.0}) == ["A"]
    assert _run("volume_pullback_trend", df2, {"A": 60 * 10000.0}) == []


def test_not_selected_when_close_far_above_lines():
    """尾盘拉升 6%：两线都远离（偏差>2%）→ 都不选。"""
    df = _with_volume_spike(_bars("A", last_close=10.6))
    cap = {"A": 60 * 10000.0}
    assert _run("volume_pullback_trend", df, cap) == []
    assert _run("volume_pullback_bullbear", df, cap) == []


def test_not_selected_when_close_below_lines():
    """收盘跌破目标线 → 不选（回踩必须收在线上）。"""
    df = _with_volume_spike(_bars("A", last_close=9.7))
    cap = {"A": 60 * 10000.0}
    assert _run("volume_pullback_trend", df, cap) == []
    assert _run("volume_pullback_bullbear", df, cap) == []


def test_not_selected_without_volume_spike():
    """20 日内无倍量柱 → 不选。"""
    df = _bars("A")  # 无量能异动
    cap = {"A": 60 * 10000.0}
    assert _run("volume_pullback_trend", df, cap) == []
    assert _run("volume_pullback_bullbear", df, cap) == []


def test_not_selected_when_duan_below_duo():
    """下跌后企稳：多空线压在短期线上方（结构不成立）→ 都不选。"""
    d0 = date(2026, 1, 1)
    closes = [12.0 - 0.02 * i for i in range(100)] + [10.0] * 30  # 跌后走平
    rows = [
        {"code": "A", "date": d0 + timedelta(days=i), "open": c, "high": c + 0.05,
         "low": c - 0.05, "close": c, "volume": 3e6 if i == 120 else 1e6}
        for i, c in enumerate(closes)
    ]
    df = pl.DataFrame(rows)
    cap = {"A": 60 * 10000.0}
    assert _run("volume_pullback_trend", df, cap) == []
    assert _run("volume_pullback_bullbear", df, cap) == []


def test_market_cap_gate():
    """市值门槛：缺失/不足 50 亿不选；≥50 亿选出。"""
    df = _with_volume_spike(_bars("A"))
    assert _run("volume_pullback_trend", df, market_cap=None) == []
    assert _run("volume_pullback_trend", df, market_cap={}) == []
    assert _run("volume_pullback_trend", df, market_cap={"A": 40 * 10000.0}) == []
    assert _run("volume_pullback_trend", df, market_cap={"A": 50 * 10000.0}) == ["A"]
    assert _run("volume_pullback_bullbear", df, market_cap={"A": 50 * 10000.0}) == ["A"]


def test_empty_data():
    for sid in ("volume_pullback_trend", "volume_pullback_bullbear"):
        result = _run(sid, make_empty_df(), {"A": 60 * 10000.0})
        assert result == []


def test_volume_spike_window_boundary():
    """倍量柱窗口含当日共 20 根：倒数第 20 根倍量 → 选中；倒数第 21 根 → 出窗不选。"""
    cap = {"A": 60 * 10000.0}
    df_in = _df_from_closes(_closes(), volumes=_spiked_volumes(idx=-20))
    df_out = _df_from_closes(_closes(), volumes=_spiked_volumes(idx=-21))
    assert _run("volume_pullback_trend", df_in, cap) == ["A"]
    assert _run("volume_pullback_bullbear", df_in, cap) == ["A"]
    assert _run("volume_pullback_trend", df_out, cap) == []
    assert _run("volume_pullback_bullbear", df_out, cap) == []


def test_hug_threshold_band():
    """贴线阈值带：偏离目标线 1.8% 选中、2.2% 不选（恰好 2.0% 为浮点不可判定区，不测）。

    用 DUAN−DUO 差 >3% 的陡涨短平盘几何，两策略各自对目标线验证，两带不重叠。
    """
    cap = {"A": 60 * 10000.0}
    base_closes = _closes(n=115, rise_bars=100, base=5.0, top=10.0)
    duan, duo = _duan_duo(_df_from_closes(base_closes))
    assert duan - duo > 0.03 * duo, f"测试数据构造失败：DUAN/DUO 价差不足 ({duan:.3f}/{duo:.3f})"

    for sid, line_idx in (("volume_pullback_trend", 0), ("volume_pullback_bullbear", 1)):
        for dev, expect in ((0.018, ["A"]), (0.022, [])):
            closes = _solve_last_close(base_closes, line_idx, dev)
            df = _df_from_closes(closes, volumes=_spiked_volumes(115))
            line = _duan_duo(df)[line_idx]
            actual = abs(df["close"][-1] - line) / line
            assert abs(actual - dev) < 5e-4, f"构造 guard 失败：dev={actual:.5f} ≠ {dev}"
            assert _run(sid, df, cap) == expect


def test_close_just_below_line_misses():
    """贴线满足（偏离 0.5%）但收盘略低于目标线 → 不选——排除只能来自「收在线上」。"""
    cap = {"A": 60 * 10000.0}
    base_closes = _closes(n=115, rise_bars=100, base=5.0, top=10.0)
    closes = _solve_last_close(base_closes, line_idx=1, dev=-0.005)
    df = _df_from_closes(closes, volumes=_spiked_volumes(115))
    _, duo = _duan_duo(df)
    close = df["close"][-1]
    assert close < duo, "构造 guard 失败：收盘应低于多空线"
    assert abs(close - duo) / duo < 0.02, "构造 guard 失败：贴线判据应满足（偏离<2%）"
    assert _run("volume_pullback_bullbear", df, cap) == []
    assert _run("volume_pullback_trend", df, cap) == []


def test_pullback_selects_across_ex_date_with_adj_factor():
    """除权（1拆2）落在 MA114 窗口内：带 adj_factor 按前复权贴线 → 双双命中；
    无 adj_factor 退化为原价——除权前高价混入多空线（≈12.75 vs 正确 ≈9.86），
    DUAN<DUO 结构反转 → 双双不选（即 docstring 主张「除权窗口内不失真」的反例）。

    raw×(adj/最新adj) 逐位复原纯 qfq 序列（×2 与 ×0.5 均为精确浮点运算），
    故两口径的指标应 bitwise 一致。
    """
    qfq = _closes()                                        # 前复权形态：涨后平，尾平 10.0
    raw = [c * 2 if i < 100 else c for i, c in enumerate(qfq)]   # 拆股前价格 ×2
    adj = [1.0 if i < 100 else 2.0 for i in range(len(qfq))]
    cap = {"A": 60 * 10000.0}

    df_with_adj = _df_from_closes(raw, volumes=_spiked_volumes(), adj_factor=adj)
    df_plain_qfq = _df_from_closes(qfq, volumes=_spiked_volumes())
    df_raw_only = _df_from_closes(raw, volumes=_spiked_volumes())

    # 复原无损：raw+adj_factor 与纯 qfq 序列的指标逐位一致
    plain = compute_b1_columns(df_plain_qfq).row(-1, named=True)
    with_adj = compute_b1_columns(df_with_adj).row(-1, named=True)
    assert with_adj["_b1_close_qfq"] == plain["_b1_close_qfq"]
    assert with_adj["_b1_duan_qi"] == plain["_b1_duan_qi"]
    assert with_adj["_b1_duo_kong"] == plain["_b1_duo_kong"]

    assert _run("volume_pullback_trend", df_with_adj, cap) == ["A"]
    assert _run("volume_pullback_bullbear", df_with_adj, cap) == ["A"]

    duan_raw, duo_raw = _duan_duo(df_raw_only)
    assert duo_raw - duan_raw > 0.03 * duo_raw, \
        f"构造 guard 失败：原价口径应结构反转 ({duan_raw:.3f}/{duo_raw:.3f})"
    assert _run("volume_pullback_trend", df_raw_only, cap) == []
    assert _run("volume_pullback_bullbear", df_raw_only, cap) == []
