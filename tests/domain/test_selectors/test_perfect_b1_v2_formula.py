"""B1战法V2（perfect_b1_v2）通达信公式等价性测试。

测试内用朴素的 Python 逐行实现实现原公式（独立于 selector 的 polars 实现），
断言 selector 选股结果与公式逐行一致。通达信语义：
- SMA(X,N,M)：Y=(M*X+(N-M)*Y')/N，首值=X[0]
- MA/LLV/HHV/SUM/COUNT 窗口不足 N 时用已有数据（partial window）
- REF(X,1)=前一根
"""
from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.protocol import SelectionContext
from .helpers import make_perfect_b1_defn


# ---------------------------------------------------------------------------
# 参考实现：通达信 B1 公式，逐行朴素翻译
# ---------------------------------------------------------------------------

def _sma(seq, n, m):
    y = [seq[0]]
    for t in range(1, len(seq)):
        y.append((m * seq[t] + (n - m) * y[-1]) / n)
    return y


def _ma(seq, n):
    return [sum(seq[max(0, i - n + 1):i + 1]) / len(seq[max(0, i - n + 1):i + 1]) for i in range(len(seq))]


def _llv(seq, n):
    return [min(seq[max(0, i - n + 1):i + 1]) for i in range(len(seq))]


def _hhv(seq, n):
    return [max(seq[max(0, i - n + 1):i + 1]) for i in range(len(seq))]


def _count(seq, n):
    return [sum(1 for x in seq[max(0, i - n + 1):i + 1] if x) for i in range(len(seq))]


def _rsum(seq, n):
    return [sum(seq[max(0, i - n + 1):i + 1]) for i in range(len(seq))]


def reference_b1_last_day(df: pl.DataFrame) -> dict[str, bool]:
    """对每只股票返回最后一天 B1 是否命中（公式逐行实现）。"""
    out: dict[str, bool] = {}
    for g in df.partition_by("code"):
        code = g["code"][0]
        g = g.sort("date")
        cs, os_, hs, ls, vs = (g[c].to_list() for c in ("close", "open", "high", "low", "volume"))
        n = len(cs)
        prev_c = [None] + cs[:-1]
        prev_v = [None] + vs[:-1]

        real_yang = [c > o and not (pc is not None and c < pc) for c, o, pc in zip(cs, os_, prev_c)]
        real_yin = [c < o and not (pc is not None and c > pc) for c, o, pc in zip(cs, os_, prev_c)]

        rsv = [(c - lv) / (hv - lv + 1e-10) * 100 for c, hv, lv in zip(cs, _hhv(hs, 9), _llv(ls, 9))]
        k = _sma(rsv, 3, 1)
        d = _sma(k, 3, 1)
        j = [3 * kk - 2 * dd for kk, dd in zip(k, d)]
        j_ok = [x <= 13 for x in j]

        vol_yang1 = _rsum([v * y for v, y in zip(vs, real_yang)], 57)
        vol_yin1 = _rsum([v * y for v, y in zip(vs, real_yin)], 57)
        vol_yang2 = _rsum([v * y for v, y in zip(vs, real_yang)], 14)
        vol_yin2 = _rsum([v * y for v, y in zip(vs, real_yin)], 14)
        yangyin_ok1 = [a > 1.25 * b for a, b in zip(vol_yang1, vol_yin1)]
        yangyin_ok2 = [a > 2.25 * b for a, b in zip(vol_yang2, vol_yin2)]

        o85 = [lv + 0.95 * (hv - lv) for hv, lv in zip(_hhv(os_, 21), _llv(os_, 21))]
        top15o = [o >= x for o, x in zip(os_, o85)]
        fd15 = [pc is not None and pv is not None and c < pc and c <= o and v >= 1.2 * pv
                for c, o, pc, v, pv in zip(cs, os_, prev_c, vs, prev_v)]
        cnt28 = _count([a and b for a, b in zip(top15o, fd15)], 21)
        good28 = [x <= 0 for x in cnt28]

        avg40 = _ma(vs, 40)
        plry = [pv is not None and v > 1.95 * pv and c > o and v > a40
                for c, o, v, pv, a40 in zip(cs, os_, vs, prev_v, avg40)]
        plry_cnt = [a >= 2 or b >= 4 for a, b in
                    zip(_count(plry, 14), _count(plry, 57))]
        plry_first = [p and not (plry[i - 1] if i > 0 else False) for i, p in enumerate(plry)]
        plry_cont = [p and (plry[i - 1] if i > 0 else False) for i, p in enumerate(plry)]
        pre_not_realyin = [not (real_yin[i - 1] if i > 0 else False) for i in range(n)]
        half_down = [pnr and pc is not None and pv is not None and c < pc and v <= 0.5 * pv
                     for pnr, c, pc, v, pv in zip(pre_not_realyin, cs, prev_c, vs, prev_v)]
        three_sum_ok = [a + b + c >= 4 for a, b, c in
                        zip(_count(plry_first, 57), _count(plry_cont, 57), _count(half_down, 57))]

        maxvol28 = _hhv(vs, 28)
        max28_bad = [v == mv and y for v, mv, y in zip(vs, maxvol28, real_yin)]
        max28_ok = [x == 0 for x in _count(max28_bad, 28)]

        a1 = [p and jo and g28 and t3 and m28 and (y1 or y2)
              for p, jo, g28, t3, m28, y1, y2 in
              zip(plry_cnt, j_ok, good28, three_sum_ok, max28_ok, yangyin_ok1, yangyin_ok2)]

        hmshortwl = _sma(_sma(cs, 40, 4), 100, 50)
        hmlongyl = [0.5 * (0.2 * a + 0.3 * b + 0.3 * c + 0.2 * d) +
                    0.5 * (0.4 * e + 0.25 * f + 0.25 * g + 0.1 * h)
                    for a, b, c, d, e, f, g, h in
                    zip(_ma(cs, 12), _ma(cs, 24), _ma(cs, 52), _ma(cs, 108),
                        _ma(cs, 20), _ma(cs, 40), _ma(cs, 80), _ma(cs, 160))]

        b1 = [h1 >= h2 * 0.985 and c >= h2 * 0.985 and a
              for h1, h2, c, a in zip(hmshortwl, hmlongyl, cs, a1)]
        out[code] = bool(b1[-1])
    return out


# ---------------------------------------------------------------------------
# 构造数据
# ---------------------------------------------------------------------------

def _df_for(code: str, days: list[dict]) -> pl.DataFrame:
    n = len(days)
    d0 = date(2026, 1, 1)
    return pl.DataFrame({
        "code": [code] * n,
        "date": [d0 + timedelta(days=i) for i in range(n)],
        "open": [x["open"] for x in days],
        "high": [x["high"] for x in days],
        "low": [x["low"] for x in days],
        "close": [x["close"] for x in days],
        "volume": [x["volume"] for x in days],
    })


def _craft_formula_hit() -> list[dict]:
    """170 日：稳步上行 + 9 次放量阳线（57 日内）+ 深回调至 9 日低位，末日再跌 3%。

    末日跌 3%：旧实现涨跌幅门 ±2% 会拒绝；公式无此限制（深回调同时压 J 至低位）。
    """
    n = 170
    base = 10.0
    days = []
    plry_idx = {114, 120, 126, 132, 138, 144, 150, 156, 160}
    for t in range(n):
        trend = base * (1 + 0.004 * t)          # 稳步上行（较陡，保证回调后 C 仍在长均线上方）
        if t in plry_idx:
            o = trend * 0.995
            c = trend * 1.01
            v = 5e6
        elif 162 <= t <= 169:                    # 深回调：每日 -0.5%
            o = trend * 0.995
            c = trend * 0.99 * (1 - 0.005 * (t - 162))
            v = 0.4e6
        elif t == 161:                           # 回调前一日：平量阳线（供 HALF_DOWN 前条件）
            o = trend * 0.998
            c = trend * 1.002
            v = 1.0e6
        else:
            o = trend * 0.998
            c = trend * 1.002
            v = 0.6e6
        if t == n - 1:
            # 最后一天：相对前收跌 3%，1% 振幅（旧实现涨跌幅门拒绝）
            prev_close = days[-1]["close"]
            c = prev_close * 0.97
            o = c * 1.005
            v = 0.4e6
            hi = c * 1.005
            lo = c * 0.995
        else:
            hi = max(o, c) * 1.005
            lo = min(o, c) * 0.995
        days.append({"open": o, "high": hi, "low": lo, "close": c, "volume": v})
    return days


def _craft_old_hit_short_history() -> list[dict]:
    """30 日缓慢阴跌：J 低位 + 振幅小 + 涨跌幅窄（旧实现命中），但历史不足 160 日（公式不命中）。"""
    n = 30
    days = []
    for t in range(n):
        c = 10.0 * (1 - 0.004 * t)
        o = c * 1.002
        hi = c * 1.01
        lo = c * 0.99
        days.append({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e6})
    return days


def _run_selector(df: pl.DataFrame, market_cap: dict[str, float]) -> list[str]:
    defn = make_perfect_b1_defn()
    sel = defn.selector_class(defn)
    warmup = sel.warmup(df)
    ctx = SelectionContext(
        trade_date=df["date"].max(),
        market_data=df,
        candidate_codes=df["code"].unique().to_list(),
        market_cap=market_cap,
    )
    return sorted(sel.select_day(ctx, warmup).selected_codes)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_matches_reference_on_formula_hit_despite_pct_gate():
    """公式命中（最后一天跌 3%，旧实现的涨跌幅门 ±2% 会拒绝）——selector 必须命中。"""
    df = _df_for("600001", _craft_formula_hit())
    ref = reference_b1_last_day(df)
    assert ref["600001"] is True, "参考实现应命中（构造数据应触发公式）"
    assert _run_selector(df, {"600001": 600000.0}) == ["600001"]


def test_matches_reference_rejects_short_history():
    """历史不足 160 日：公式不命中（长均线缺失），旧实现会命中——selector 必须拒绝。"""
    df = _df_for("000002", _craft_old_hit_short_history())
    ref = reference_b1_last_day(df)
    assert ref["000002"] is False, "参考实现应不命中（HMLONGYL 需要 160 日）"
    assert _run_selector(df, {"000002": 600000.0}) == []


def test_matches_reference_on_mixed_pool():
    """混合池：每只股票 selector 与参考实现逐日一致（取最后一天）。"""
    a = _df_for("600001", _craft_formula_hit())
    b = _df_for("000002", _craft_old_hit_short_history())
    flat = _df_for("000003", _craft_formula_hit())  # 横盘参考
    df = pl.concat([a, b, flat])
    ref = reference_b1_last_day(df)
    selected = _run_selector(df, {"600001": 600000.0, "000002": 600000.0, "000003": 600000.0})
    expected = sorted(c for c, hit in ref.items() if hit)
    assert selected == expected


def test_mvok_filters_below_50_yi():
    """市值 < 50 亿（circ_mv 万元 < 500000）不入选，≥ 50 亿入选。"""
    df = _df_for("600001", _craft_formula_hit())
    assert _run_selector(df, {"600001": 499999.0}) == []
    assert _run_selector(df, {"600001": 500000.0}) == ["600001"]


def test_requires_market_cap_flag():
    """声明 REQUIRES_MARKET_CAP，触发 runner 按日拉取流通市值。"""
    defn = make_perfect_b1_defn()
    assert defn.selector_class.REQUIRES_MARKET_CAP is True
