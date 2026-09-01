"""同步执行器：只执行与统计，不做任何决策（spec §3.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from trendradar.domain.market.sync.selfcheck import doubtful_by_row_count
from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.fetch import (
    fetch_day_by_date,
    filter_excluded_boards,
)
from trendradar.infrastructure.tushare.stocklist import EffectiveList


@dataclass
class IncrementalResult:
    claimed_days: list[date] = field(default_factory=list)
    doubtful_days: list[date] = field(default_factory=list)
    all_days: pl.DataFrame | None = None   # aborted 时为 None（丢弃，不写盘）
    aborted: bool = False
    cancelled: bool = False
    failure_kind: FailureKind | None = None
    abort_reason: str | None = None


def run_incremental(
    pro,
    missing_days: list[date],
    effective: EffectiveList,
    exclude_boards,
    bucket=None,
    progress=None,
    cancel_check=None,
) -> IncrementalResult:
    """串行按日拉取（2 次调用/天）。任何非断言①异常立即中止整批（spec §3.5）。"""
    result = IncrementalResult()
    frames: list[pl.DataFrame] = []
    total = len(missing_days)
    for idx, day in enumerate(missing_days, start=1):
        if cancel_check and cancel_check():
            result.aborted = True
            result.cancelled = True
            result.abort_reason = "cancelled"
            return result
        if progress:
            progress(idx, total, str(day))
        fr = fetch_day_by_date(pro, day, bucket=bucket, cancel_check=cancel_check)
        if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
            result.aborted = True
            result.failure_kind = fr.kind
            result.abort_reason = fr.error
            return result
        df = filter_excluded_boards(fr.df if fr.df is not None else pl.DataFrame(),
                                    exclude_boards)
        # 含 doubtful 日：真实交易数据照常累积（INV-4 幂等）；空帧无列，不入 concat
        if df.width > 0:
            frames.append(df)
        doubtful = doubtful_by_row_count({day: df.height}, list(effective.rows))
        if doubtful:
            result.doubtful_days.extend(doubtful)
        else:
            result.claimed_days.append(day)
    result.all_days = pl.concat(frames) if frames else pl.DataFrame()
    return result
