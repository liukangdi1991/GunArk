"""On-demand float market cap (daily_basic.circ_mv, 万元) with in-memory cache.

Fetched per trade_date at selection time; dates are immutable facts, so the
process-level cache needs no TTL. circ_mv unit: 万元 (verified 2026-08-23:
600519≈1.59e8万=1.59万亿, 000001≈2214亿, circ_mv/total_mv ∈ (0,1]).
"""

from __future__ import annotations

from datetime import date

_MARKET_CAP_CACHE: dict[date, dict[str, float]] = {}


def daily_basic_circ_mv(pro, trade_date: date) -> dict[str, float]:
    """Full-market float market cap for one trade day: {code: circ_mv_万元}."""
    if trade_date in _MARKET_CAP_CACHE:
        return _MARKET_CAP_CACHE[trade_date]
    resp = pro.daily_basic(
        trade_date=trade_date.strftime("%Y%m%d"),
        fields="ts_code,circ_mv",
    )
    result: dict[str, float] = {}
    for row in resp.to_dict(orient="records"):
        code = str(row["ts_code"])[:6]
        mv = row["circ_mv"]
        if mv is not None and mv == mv:  # skip NaN
            result[code] = float(mv)
    _MARKET_CAP_CACHE[trade_date] = result
    return result
