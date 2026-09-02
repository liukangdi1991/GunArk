"""同步执行器：只执行与统计，不做任何决策（spec §3.1）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from trendradar.domain.market.sync.selfcheck import doubtful_by_row_count
from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.fetch import (
    fetch_code_range,
    fetch_day_by_date,
    filter_excluded_boards,
    shard_ranges,
)
from trendradar.infrastructure.tushare.stocklist import EffectiveList
from trendradar.infrastructure.tushare.writer import (
    atomic_write_parquet,
    upsert_code_file,
)


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


@dataclass(frozen=True)
class StockOutcome:
    code: str
    ok: bool
    kind: FailureKind | None = None   # ok=True 时可为 OK_EMPTY；失败时为分类
    error: str | None = None


def run_full(
    pro,
    tasks: list[tuple[str, date, date]],
    staging_dir: Path,
    bucket=None,
    progress=None,
    cancel_check=None,
    max_workers: int = 6,
) -> tuple[list[StockOutcome], bool]:
    """全量执行：逐只写 staging（本地 bars 一字不动）。返回 (outcomes, cancelled)。

    取消时立即停止派发；已写入的 staging 文件原地保留供续传（spec §3.6）。
    """
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[StockOutcome] = []
    cancelled = False
    total = len(tasks)

    def fetch_one(code: str, start: date, end: date) -> StockOutcome:
        frames = []
        for seg_start, seg_end in shard_ranges(start, end):
            if cancel_check and cancel_check():
                return StockOutcome(code, False, FailureKind.ENV, "cancelled")
            fr = fetch_code_range(pro, code, seg_start, seg_end,
                                  bucket=bucket, cancel_check=cancel_check)
            if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
                return StockOutcome(code, False, fr.kind, fr.error)
            if fr.df is not None and not fr.df.is_empty():
                frames.append(fr.df)
        if not frames:
            return StockOutcome(code, True, FailureKind.OK_EMPTY)  # 钳制后仍空：视为成功
        df = pl.concat(frames).sort("date")
        atomic_write_parquet(df, staging_dir / f"{code}.parquet")
        return StockOutcome(code, True)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, c, s, e): c for c, s, e in tasks}
        for i, fut in enumerate(as_completed(futures), start=1):
            code = futures[fut]
            try:
                outcome = fut.result()
            except Exception as e:
                outcome = StockOutcome(code, False, FailureKind.UNKNOWN, str(e))
            outcomes.append(outcome)
            if progress:
                progress(i, total, code)
            if cancel_check and cancel_check():
                cancelled = True
                for pending in futures:
                    pending.cancel()
                break
    return outcomes, cancelled


def run_backfill(
    pro,
    tasks: list[tuple[str, date, date]],
    bars_dir: Path,
    bucket=None,
    progress=None,
    cancel_check=None,
) -> list[StockOutcome]:
    """指定代码补齐（INV-3：永不触碰日账本），串行，直接 upsert 进 bars。"""
    bars_dir = Path(bars_dir)
    outcomes: list[StockOutcome] = []
    total = len(tasks)
    for idx, (code, start, end) in enumerate(tasks, start=1):
        if cancel_check and cancel_check():
            outcomes.append(StockOutcome(code, False, FailureKind.ENV, "cancelled"))
            break
        if progress:
            progress(idx, total, code)
        frames = []
        failed: StockOutcome | None = None
        for seg_start, seg_end in shard_ranges(start, end):
            fr = fetch_code_range(pro, code, seg_start, seg_end,
                                  bucket=bucket, cancel_check=cancel_check)
            if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
                failed = StockOutcome(code, False, fr.kind, fr.error)
                break
            if fr.df is not None and not fr.df.is_empty():
                frames.append(fr.df)
        if failed is not None:
            outcomes.append(failed)
            continue
        if frames:
            df = pl.concat(frames).sort("date")
            upsert_code_file(bars_dir / f"{code}.parquet", df)
        outcomes.append(StockOutcome(code, True))
    return outcomes
