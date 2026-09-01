"""build_plan：stage-2 唯一决策出口（纯函数，零 I/O，零 now()）。"""

from __future__ import annotations

from datetime import date, datetime

from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    PlanKind,
    SyncPlan,
    latest_tradeable_day,
)


def stale_days(calendar_days: set[date], done_days: set[date], latest: date | None) -> int:
    """从 latest 沿官方日历反向回溯到第一个已入账日的交易日数。
    公开导出：presenters 的新鲜度面板复用同一口径（spec §4）。"""
    if latest is None:
        return 0
    ordered = sorted(d for d in calendar_days if d <= latest)
    count = 0
    for d in reversed(ordered):
        if d in done_days:
            break
        count += 1
    return count


def build_plan(
    today_cn: date,
    now_cn: datetime,
    calendar_days: set[date],
    done_days: set[date],
    ledger_suspect: bool,
    request: dict,
) -> SyncPlan:
    """决策矩阵（自上而下，首个匹配生效），见 spec §3.4。"""
    if not calendar_days or max(calendar_days) < today_cn:
        return SyncPlan(PlanKind.BLOCKED, None, [], 0,
                        "官方日历未就绪/过期，拒绝判断新鲜度")

    latest = latest_tradeable_day(calendar_days, now_cn)
    missing = sorted(
        d for d in calendar_days
        if latest is not None and BASELINE_START <= d <= latest and d not in done_days
    )
    stale = stale_days(calendar_days, done_days, latest)

    if request.get("codes"):
        return SyncPlan(PlanKind.BACKFILL_CODES, latest, missing, stale,
                        "指定代码补齐（不参与日账本）")
    if request.get("force"):
        return SyncPlan(PlanKind.FULL, latest, missing, stale,
                        "用户显式要求全量重建")
    if not done_days:
        return SyncPlan(PlanKind.FULL, latest, missing, stale, "首次建库")
    if ledger_suspect:
        return SyncPlan(PlanKind.REBUILD_REQUIRED, latest, missing, stale,
                        "账本曾被自检判为不可信，需人工确认重建")
    if not missing:
        return SyncPlan(PlanKind.UPTODATE, latest, [], 0, "已覆盖至最近可交易日")
    return SyncPlan(PlanKind.INCREMENTAL, latest, missing, stale,
                    f"待补 {len(missing)} 个交易日")
