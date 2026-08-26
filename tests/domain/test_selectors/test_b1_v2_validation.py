"""B1战法V2（perfect_b1_v2）独立验证。

生产实现是 polars 向量化；这里的参考实现是朴素逐行翻译（与生产写法完全
独立），在随机数据上逐日交叉验证，另覆盖除权/空/一字板等边界。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from trendradar.domain.strategy.formulas.b1_v2 import compute_b1_v2_columns


# ---------------------------------------------------------------------------
# 参考实现（返回每个交易日是否命中）
# ---------------------------------------------------------------------------

def _sma(seq, n, m):
    y = [seq[0]]
    for x in seq[1:]:
        y.append((m * x + (n - m) * y[-1]) / n)
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


def reference_b1_series(df: pl.DataFrame) -> dict[str, list[bool]]:
    """每只股票逐日 B1 命中序列（通达信公式朴素翻译，不用 pre_close）。"""
    out: dict[str, list[bool]] = {}
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

        out[code] = [h1 >= h2 * 0.985 and c >= h2 * 0.985 and a
                     for h1, h2, c, a in zip(hmshortwl, hmlongyl, cs, a1)]
    return out


# ---------------------------------------------------------------------------
# 随机数据生成
# ---------------------------------------------------------------------------

def _random_df(seed: int = 42, n_codes: int = 5, max_days: int = 180) -> pl.DataFrame:
    import numpy as np

    rng = np.random.default_rng(seed)
    rows = []
    start = date(2026, 1, 1)
    for i in range(n_codes):
        code = f"{i:06d}"
        n = int(rng.integers(3, max_days + 1))  # 含极短历史
        close = 10.0 * np.exp(np.cumsum(0.02 * rng.standard_normal(n)))
        open_ = close * (1 + 0.01 * rng.standard_normal(n))
        high = np.maximum(open_, close) * (1 + 0.01 * np.abs(rng.standard_normal(n)))
        low = np.minimum(open_, close) * (1 - 0.01 * np.abs(rng.standard_normal(n)))
        vol = 1e6 * (1 + 0.5 * rng.standard_normal(n))
        vol = np.maximum(vol, 1e3)
        for d in range(n):
            rows.append({
                "code": code,
                "date": start + timedelta(days=d),
                "open": float(open_[d]), "high": float(high[d]),
                "low": float(low[d]), "close": float(close[d]),
                "volume": float(vol[d]),
            })
    return pl.DataFrame(rows).sort(["code", "date"])


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_random_data_matches_reference_daily():
    df = _random_df()
    prod = compute_b1_v2_columns(df)
    ref = reference_b1_series(df)

    for code, series in ref.items():
        got = prod.filter(pl.col("code") == code)["_b1_signal"].to_list()
        assert len(got) == len(series)
        for i, (g, r) in enumerate(zip(got, series)):
            assert bool(g) == bool(r), (
                f"{code} day {i}: prod={g} ref={r}"
            )


def test_empty_frame_returns_false_column():
    df = pl.DataFrame(schema={"code": pl.Utf8})
    out = compute_b1_v2_columns(df)
    assert "_b1_signal" in out.columns


def test_single_row_does_not_crash():
    df = pl.DataFrame([{
        "code": "000001", "date": date(2026, 1, 1),
        "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2, "volume": 1e6,
    }])
    out = compute_b1_v2_columns(df)
    assert out["_b1_signal"].to_list() == [False]


def test_zero_span_limit_up_does_not_crash():
    """high==low（一字板）→ RSV 0-span；fill_nan(50) 不应崩。"""
    rows = []
    start = date(2026, 1, 1)
    for d in range(30):
        rows.append({
            "code": "000001", "date": start + timedelta(days=d),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 1e6,
        })
    df = pl.DataFrame(rows)
    out = compute_b1_v2_columns(df)
    assert len(out) == 30


def test_ex_dividend_uses_pre_close():
    """除权日：可比昨收（pre_close）≠ 前日收盘——REAL_YANG 必须用 pre_close。

    10 送 10：前日收盘 20，除权日可比昨收 10，当日 open=10、close=10.5。
    用 pre_close(10)：相对可比昨收上涨 → 阳线；用原始昨收(20)：相对下跌 → 非阳。
    """
    rows = [
        {"code": "000001", "date": date(2026, 1, 1), "open": 20.0, "high": 20.5,
         "low": 19.5, "close": 20.0, "volume": 1e6, "pre_close": None},
        {"code": "000001", "date": date(2026, 1, 2), "open": 10.0, "high": 10.8,
         "low": 9.9, "close": 10.5, "volume": 1e6, "pre_close": 10.0},
    ]
    df = pl.DataFrame(rows)
    out = compute_b1_v2_columns(df)
    # 除权日（第 2 行）：REAL_YANG 应为 True（10.5 > 10 且 10.5 >= pre_close 10）
    assert bool(out["real_yang"].to_list()[1]) is True
    # 第 1 行 open==close 平盘且 pre_close 为 None：非阳线（20>20 为 False）
    assert bool(out["real_yang"].to_list()[0]) is False
