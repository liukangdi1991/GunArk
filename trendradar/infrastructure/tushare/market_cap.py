"""On-demand float market cap (daily_basic.circ_mv, 万元) with in-memory cache.

Fetched per trade_date at selection time; dates are immutable facts, so the
process-level cache needs no TTL. circ_mv unit: 万元 (verified 2026-08-23:
600519≈1.59e8万=1.59万亿, 000001≈2214亿, circ_mv/total_mv ∈ (0,1]).
"""

from __future__ import annotations

import threading
from datetime import date
_MARKET_CAP_CACHE: dict[date, dict[str, float]] = {}
_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: dict[date, dict[str, dict[str, float]]] = {}
_SNAPSHOT_FIELDS = "ts_code,circ_mv,total_mv,turnover_rate,pe_ttm,pb"


def daily_basic_circ_mv(pro, trade_date: date) -> dict[str, float]:
    """Full-market float market cap for one trade day: {code: circ_mv_万元}.

    并发：锁内查缓存、锁外拉取（网络 IO 不持锁）；同时 miss 最多重复拉一次，
    结果幂等无害。接口返回 None（异常/限流）时返回空 dict 且**不缓存**——
    空结果让市值 gate 可见地降级，同时保留下次调用重试的机会。
    """
    with _CACHE_LOCK:
        if trade_date in _MARKET_CAP_CACHE:
            return _MARKET_CAP_CACHE[trade_date]
    resp = pro.daily_basic(
        trade_date=trade_date.strftime("%Y%m%d"),
        fields="ts_code,circ_mv",
    )
    if resp is None:
        return {}
    result: dict[str, float] = {}
    for row in resp.to_dict(orient="records"):
        code = str(row["ts_code"])[:6]
        mv = row["circ_mv"]
        if mv is not None and mv == mv:  # skip NaN
            result[code] = float(mv)
    with _CACHE_LOCK:
        _MARKET_CAP_CACHE[trade_date] = result
    return result


def daily_basic_snapshot(pro, trade_date: date) -> dict[str, dict[str, float]]:
    """全市场个股快照（流通市值/总市值/换手率/PE/PB），按日进程内缓存。

    与 circ_mv 同源（daily_basic），字段更全供个股页展示；接口返回 None
    （异常/限流）时返回空 dict 且不缓存——保留重试机会。
    """
    with _CACHE_LOCK:
        if trade_date in _SNAPSHOT_CACHE:
            return _SNAPSHOT_CACHE[trade_date]
    resp = pro.daily_basic(
        trade_date=trade_date.strftime("%Y%m%d"),
        fields=_SNAPSHOT_FIELDS,
    )
    if resp is None:
        return {}
    result: dict[str, dict[str, float]] = {}
    for row in resp.to_dict(orient="records"):
        code = str(row["ts_code"])[:6]
        entry: dict[str, float] = {}
        for key in ("circ_mv", "total_mv", "turnover_rate", "pe_ttm", "pb"):
            value = row.get(key)
            if value is not None and value == value:  # skip NaN
                entry[key] = float(value)
        result[code] = entry
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE[trade_date] = result
    return result
