"""4 条自检断言（纯函数，输入内存副本 + 集合）。见 spec §3.8。"""

from __future__ import annotations

from datetime import date

import polars as pl

ROW_COUNT_RATIO = 0.75

_EFFECTIVE_ROW = tuple[date, date | None]  # (list_date, delist_date)


def expected_trading_count(effective: list[_EFFECTIVE_ROW], day: date) -> int:
    """expected(d)：有效清单中 d 日应市的股票数。"""
    return sum(
        1 for list_d, delist_d in effective
        if list_d <= day and (delist_d is None or delist_d >= day)
    )


THRESHOLD_BANDS: tuple[tuple[int, float], ...] = (
    (2017, 0.85),   # 2017 ≤ year < 2019：重组停牌泛滥期（p1≈0.89-0.91）
    (2019, 0.95),   # year ≥ 2019：常态期（p1≈0.972-0.983）
)
DEFAULT_THRESHOLD = ROW_COUNT_RATIO  # 0.75：year < 2017（含 2015 股灾段）


def threshold_for(day: date) -> float:
    """断言①分段阈值：按被检日期所处年代取值（2026-09-09 spec v2 §D1/R18）。

    分年实测 p1：2015=0.520 / 2016=0.876 / 2017-2018≈0.89-0.91 / 2019+=0.972+；
    各段阈值取略低于该段 p1，留 2-4% 缓冲。
    """
    t = DEFAULT_THRESHOLD
    for start_year, v in THRESHOLD_BANDS:
        if day.year >= start_year:
            t = v
    return t


def reconciliation_ok(actual: int, expected_alive: int, suspended: int,
                      tol_pct: float = 0.02, tol_abs: int = 5) -> bool:
    """suspend_d 对账判定（spec v2 §D2/R19）：actual ≥ 应成交 − max(5, 2%×应成交)。

    单边容差：缺口方向（应成交却无 bar）才可能是拉取截断；多出方向是
    suspend_d 漏记/盘中复牌的真实 bar，无害。2% 经 2015-2019 全史 1219 天
    实测 100% 覆盖（2026-09-09）。
    """
    expected_traded = max(0, expected_alive - suspended)
    return actual >= expected_traded - max(tol_abs, tol_pct * expected_traded)


def doubtful_detail(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    already_booked: set[date] | None = None,
) -> list[dict]:
    """断言①明细：低于当日分段阈值的日子（升序），含 actual/expected/ratio。

    already_booked：账本已有日豁免（2cce2037）；阈值按日分段（threshold_for）。
    """
    booked = already_booked or set()
    out = []
    for d in sorted(day_rows):
        if d in booked:
            continue
        n = day_rows[d]
        e = expected_trading_count(effective, d)
        if n < threshold_for(d) * e:
            out.append({"day": d, "actual": n, "expected": e,
                        "ratio": round(n / e, 4) if e else None})
    return out


def doubtful_by_row_count(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    already_booked: set[date] | None = None,
) -> list[date]:
    """断言①：行数 < threshold_for(d) × expected(d) 的日期（升序）。"""
    return [r["day"] for r in doubtful_detail(day_rows, effective, already_booked)]


def coverage_ok(calendar: set[date], claimed: set[date]) -> bool:
    """断言②：读回实测日历 ⊇ 声称日集合。"""
    return claimed <= calendar


def file_structure_ok(df: pl.DataFrame) -> bool:
    """断言③：日期严格递增（含无重复）+ OHLC 无 NaN。"""
    if df.is_empty():
        return True
    dates = df["date"].to_list()
    if any(a >= b for a, b in zip(dates, dates[1:])):
        return False
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            return False
        if df[col].null_count() > 0 or bool(df[col].is_nan().any()):
            return False
    return True


def ledger_subset_ok(new_done: set[date], calendar: set[date]) -> bool:
    """断言④：新增入账日 ⊆ 读回实测日历。"""
    return new_done <= calendar
