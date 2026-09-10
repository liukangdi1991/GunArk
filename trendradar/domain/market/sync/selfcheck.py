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


def doubtful_detail(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    threshold: float = ROW_COUNT_RATIO,
    already_booked: set[date] | None = None,
) -> list[dict]:
    """断言①明细：低于阈值的日子（升序），含 actual/expected/ratio。

    already_booked：账本已有日豁免——此前已通过检查或经人工确认入账，全量
    重建重拉历史时行数不会变化，不应重复拦截（否则每次全量都必然在同样的
    历史停牌日上失败一次；2026-09-09 审查）。
    """
    booked = already_booked or set()
    out = []
    for d in sorted(day_rows):
        if d in booked:
            continue
        n = day_rows[d]
        e = expected_trading_count(effective, d)
        if n < threshold * e:
            out.append({"day": d, "actual": n, "expected": e,
                        "ratio": round(n / e, 4) if e else None})
    return out


def doubtful_by_row_count(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    threshold: float = ROW_COUNT_RATIO,
    already_booked: set[date] | None = None,
) -> list[date]:
    """断言①：行数 < threshold × expected(d) 的日期（升序）。"""
    return [r["day"] for r in doubtful_detail(day_rows, effective, threshold, already_booked)]


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
