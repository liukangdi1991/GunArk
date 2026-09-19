"""放量回踩（趋势线/多空线）选择行为验证。

语义（用户 2026-09-18 确认）：流通市值≥50亿 + 短期线>多空线 + 20日内存在倍量柱
+ |收盘−目标线|/目标线 < 2%（贴线）+ 收盘≥目标线。
目标线：放量回踩趋势线=短期线（DUAN_QI=EMA(EMA(C,10),10)）；放量回踩多空线=多空线
（4-MA 14/28/57/114）。与 B1 共用公式层，指标同用前复权价。

测试数据构造：先涨后平的序列让 DUAN≈收盘、DUO 略低（上涨段混入均线）；
用 compute_b1_columns 实测 DUAN/DUO 后再定位最后一根收盘，保证「贴线/远离」
判据确定性成立。
"""

from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.formulas.b1 import compute_b1_columns
from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import get_defn, make_empty_df


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


def _bars(code: str, n=130, rise_bars=100, base=8.5, top=10.0, volumes=None, last_close=None):
    """先线性上涨 rise_bars 根到 top，再平盘；volume 可逐根覆盖。"""
    d0 = date(2026, 1, 1)
    closes = []
    for i in range(n):
        if i < rise_bars:
            closes.append(base + (top - base) * i / max(rise_bars - 1, 1))
        else:
            closes.append(top)
    if last_close is not None:
        closes[-1] = last_close
    rows = []
    for i, c in enumerate(closes):
        v = volumes[i] if volumes else 1_000_000.0
        rows.append({
            "code": code, "date": d0 + timedelta(days=i),
            "open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": v,
        })
    return pl.DataFrame(rows)


def _duan_duo(df):
    out = compute_b1_columns(df)
    latest = out.row(-1, named=True)
    return latest["_b1_duan_qi"], latest["_b1_duo_kong"]


def _with_volume_spike(df):
    v = df["volume"].to_list()
    v[-8] = v[-8] * 3.0  # 20 日内一根倍量柱
    return df.with_columns(pl.Series("volume", v))


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
