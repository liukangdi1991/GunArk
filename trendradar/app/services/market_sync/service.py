"""市场数据同步编排：stage-1 日历 + stage-2 行情（spec §3）。

依赖方向：app → infrastructure/domain。唯一有权驱动
runner/writer/commit 的模块。
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import polars as pl

from trendradar.app.jobs.context import JobContext
from trendradar.app.services.market_sync.commit import commit_full, commit_incremental
from trendradar.domain.market.sync.planner import build_plan
from trendradar.domain.market.sync.selfcheck import (
    coverage_ok,
    doubtful_by_row_count,
    file_structure_ok,
    ledger_subset_ok,
)
from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    FailureKind,
    PlanKind,
    SHANGHAI,
)
from trendradar.infrastructure.storage.sync_store import SyncStore
from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar
from trendradar.infrastructure.tushare.client import get_pro
from trendradar.infrastructure.tushare.rate_limit import TokenBucket
from trendradar.infrastructure.tushare.runner import (
    run_backfill,
    run_full,
    run_incremental,
)
from trendradar.infrastructure.tushare.stocklist import (
    build_effective_list,
    sync_stock_list,
)
from trendradar.infrastructure.tushare.writer import (
    flush_by_code,
    readback_calendar,
    swap_in_bars,
)

FULL_FAIL_RATE_BREAKER = 0.05


def _invalidate_status_cache() -> None:
    # 函数级导入：presenters 反向依赖 service 会成环
    from trendradar.interfaces.api.presenters import invalidate_market_status_cache

    invalidate_market_status_cache()


def register_market_sync_execution(job_id: str) -> None:
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import register_execution

    with StorageConnection(storage_root()).connection() as conn:
        register_execution(conn, job_id, "market_bars_sync")
        conn.commit()


def bars_sync_worker(
    ctx: JobContext, request: dict, now_cn: datetime | None = None
) -> None:
    from trendradar.infrastructure.runtime import storage_root

    root = storage_root()
    store = SyncStore(root)
    market_dir = root / "market"
    try:
        _bars_sync_body(ctx, request, store, market_dir, now_cn)
    except Exception as e:  # 意外异常如实 failed，绝不假绿（INV-1）
        ctx.fail(f"意外错误: {e}")
    finally:
        _invalidate_status_cache()


def _bars_sync_body(ctx, request, store, market_dir, now_cn=None):
    now_cn = now_cn or datetime.now(SHANGHAI)
    today_cn = now_cn.date()
    pro = get_pro()
    bars_dir = market_dir / "bars"
    staging_dir = market_dir / "staging"
    register_market_sync_execution(ctx.job_id)

    # ---- stage-1：日历刷新（同步步骤，不经 executor，spec §3.9）----
    if not _stage1_calendar(ctx, store, pro, now_cn):
        if not store.calendar_days():
            ctx.fail("日历未就绪，行情同步已阻断")
        else:
            ctx.fail("官方日历未就绪/过期，拒绝判断新鲜度")
        return

    # 硬检（§3.3）：不满足即 failed，禁止降级继续
    max_day = store.max_calendar_day()
    if max_day is None or max_day < today_cn:
        ctx.fail("官方日历未就绪/过期，拒绝判断新鲜度")
        return

    exclude_boards = request.get("exclude_boards") or []
    meta = sync_stock_list(bars_dir)
    ctx.log(f"股票清单已刷新：{meta.height} 行")

    plan = build_plan(
        today_cn, now_cn, store.calendar_days(), store.done_days(),
        store.ledger_suspect(), request,
    )
    ctx.log(f"决策: {plan.kind.value} —— {plan.reason}")

    progress = lambda cur, total, msg: ctx.update_progress(cur, total, msg)
    cancel_check = lambda: ctx.check_cancelled()

    if plan.kind is PlanKind.BLOCKED:
        ctx.fail(plan.reason)
        return
    if plan.kind is PlanKind.REBUILD_REQUIRED:
        ctx.fail(plan.reason)
        return

    effective = build_effective_list(meta, exclude_boards, plan.latest_tradeable)
    bucket = TokenBucket()

    if plan.kind is PlanKind.BACKFILL_CODES:
        ok = _run_backfill_batch(
            ctx, pro, store, effective, request.get("codes") or [],
            bars_dir, bucket, progress, cancel_check,
        )
        if ok:
            ctx.succeed({"kind": "backfill_codes"})
        else:
            ctx.fail("指定代码补齐存在失败")
        return

    if plan.kind is PlanKind.FULL:
        _run_full(ctx, pro, store, effective, request, plan,
                  market_dir, bars_dir, staging_dir, bucket, progress, cancel_check)
        return

    # ---- UPTODATE / INCREMENTAL ----
    if plan.kind is PlanKind.INCREMENTAL:
        ok, fail_msg = _run_incremental(
            ctx, pro, store, effective, exclude_boards, plan,
            bars_dir, bucket, progress, cancel_check,
        )
    else:
        ok, fail_msg = True, None
        ctx.log(plan.reason)
    if ok:
        _tail_backfill(ctx, pro, store, effective, bars_dir, bucket, progress, cancel_check)
        ctx.succeed({"kind": plan.kind.value, "reason": plan.reason})
    else:
        ctx.fail(fail_msg)


def _stage1_calendar(ctx, store, pro, now_cn) -> bool:
    cal_job_id = ctx.store.create_job("market_calendar_sync", {})
    ctx.store.update_started_at(cal_job_id)
    try:
        fetched = _refresh_calendar(store, pro, now_cn)
    except Exception as e:
        ctx.store.set_status(cal_job_id, "failed", error=str(e))
        ctx.log(f"stage-1 日历刷新失败: {e}", level="ERROR")
        return False
    ctx.store.set_status(cal_job_id, "success", result={"fetched": len(fetched)})
    ctx.log(f"stage-1 日历刷新完成：拉取 {len(fetched)} 日")
    return True


def _refresh_calendar(store, pro, now_cn) -> list[date]:
    """表空 → BASELINE..今年年底；非空 → 只刷今年（各 1 次调用，spec §3.3）。"""
    start = BASELINE_START if store.max_calendar_day() is None else date(now_cn.year, 1, 1)
    end = date(now_cn.year, 12, 31)
    days = fetch_trade_calendar(pro, start, end)
    if not days:
        raise RuntimeError("trade_cal 返回空")
    store.insert_calendar_days(days)
    return days


def _run_incremental(ctx, pro, store, effective, exclude_boards, plan,
                     bars_dir, bucket, progress, cancel_check):
    """增量批。返回 (ok, fail_msg)；终态由调用方统一写（单次终态）。"""
    res = run_incremental(
        pro, plan.missing_days, effective, exclude_boards,
        bucket=bucket, progress=progress, cancel_check=cancel_check,
    )
    if res.cancelled:
        return False, "Cancelled by user"
    if res.aborted:
        kind = res.failure_kind.value if res.failure_kind else "?"
        return False, f"增量中止（{kind}）: {res.abort_reason}"

    all_days = res.all_days if res.all_days is not None else pl.DataFrame()

    # ③ 结构自检（内存副本，按未来文件分组）
    if not _memory_structure_ok(all_days):
        store.set_ledger_suspect(True)
        return False, "自检③失败：内存副本结构异常，整批不落账（已置 ledger_suspect）"

    # 写盘（含 doubtful 日，INV-4 幂等）
    flush_by_code(all_days, bars_dir)

    # ⑤ 读回校验 ②④
    readback = readback_calendar(bars_dir)
    claimed = set(res.claimed_days)
    if not coverage_ok(readback, claimed) or not ledger_subset_ok(claimed, readback):
        store.set_ledger_suspect(True)
        return False, "自检②④失败：读回日历与声称集合漂移（已置 ledger_suspect）"

    # ⑥ 单事务落账（仅声称日；doubtful 合并 = 旧 − 已入账 ∪ 新）
    merged_doubtful = sorted({*store.doubtful_days(), *res.doubtful_days} - claimed)
    commit_incremental(store, sorted(claimed), merged_doubtful)

    if res.doubtful_days:
        return False, (f"{len(res.doubtful_days)} 个交易日行数异常（doubtful）"
                       f"，未入账，可自愈或人工确认入账")
    return True, None


def _memory_structure_ok(all_days: pl.DataFrame) -> bool:
    if all_days.is_empty():
        return True
    for code in all_days["code"].unique().to_list():
        group = all_days.filter(pl.col("code") == code).sort("date")
        if not file_structure_ok(group):
            return False
    return True


def _run_full(ctx, pro, store, effective, request, plan,
              market_dir, bars_dir, staging_dir, bucket, progress, cancel_check):
    staging_dir.mkdir(parents=True, exist_ok=True)
    excluded = set(store.excluded_codes())
    existing = {p.stem for p in staging_dir.glob("*.parquet")}
    tasks = []
    for code in effective.codes:
        if code in excluded or code in existing:
            continue
        rng = effective.clamped_range(code)
        tasks.append((code, rng[0], rng[1]))
    ctx.log(f"全量：待拉 {len(tasks)} 只（续传剔除 {len(existing)}，出列剔除 {len(excluded)}）")

    outcomes, cancelled = run_full(
        pro, tasks, staging_dir,
        bucket=bucket, progress=progress, cancel_check=cancel_check,
    )
    if cancelled:
        ctx.fail("Cancelled by user")
        return

    failed = [o for o in outcomes if not o.ok]
    # 整批熔断（§3.7 主保险）：>5% ⇒ 环境故障 ⇒ 一律不计次，保留 staging
    if tasks and len(failed) / len(tasks) > FULL_FAIL_RATE_BREAKER:
        ctx.fail(f"整批熔断：{len(failed)}/{len(tasks)} 只失败（>5%），"
                 f"判定环境故障，本轮不计次，staging 保留续传")
        return
    for o in failed:  # 健康轮才记失败；env 不计（INV-5）
        if o.kind in (FailureKind.CODE, FailureKind.UNKNOWN):
            store.record_skip_failure(o.code, o.error, o.kind.value)

    covered = sorted(
        d for d in store.calendar_days()
        if BASELINE_START <= d <= plan.latest_tradeable
    )

    # ---- 自检：①②③ 全部对 staging（换名前，spec §3.6/§3.8）----
    readback = readback_calendar(staging_dir)
    if not coverage_ok(readback, set(covered)):
        _discard_staging(staging_dir)
        store.set_ledger_suspect(True)
        ctx.fail("全量自检②失败：staging 读回缺日，丢弃 staging，下轮从头重拉")
        return
    bad = _first_structurally_bad_file(staging_dir)
    if bad is not None:
        _discard_staging(staging_dir)
        store.set_ledger_suspect(True)
        ctx.fail(f"全量自检③失败：{bad} 结构异常，丢弃 staging，下轮从头重拉")
        return

    doubtful = doubtful_by_row_count(_staging_day_rows(staging_dir), list(effective.rows))

    # ---- 带缺口提交约束（§3.7）：卡在换名/落账之前 ----
    skipped = store.skipped_rows()
    if skipped:
        if not request.get("accept_partial_baseline"):
            ctx.fail(f"sync_skipped 非空（{len(skipped)} 只），"
                     f"带缺口提交需显式 accept_partial_baseline=true；staging 保留续传")
            return
        if any(r.get("kind") == FailureKind.ENV.value for r in skipped):
            ctx.fail("sync_skipped 含 env 残留，拒绝带缺口提交，等下轮")
            return

    # ---- 单向换名（§3.6）→ ④ → 单事务落账 ----
    swap_in_bars(market_dir)
    booked = sorted(set(covered) - set(doubtful))
    if not ledger_subset_ok(set(booked), readback_calendar(bars_dir)):
        # 全量批 ④ 失败：不置 suspect（置则逼迫重建已换名成功的数据）
        ctx.fail("全量自检④失败：换名后未落账，下轮增量幂等自愈")
        return
    try:
        commit_full(store, booked, doubtful)
    except Exception as e:
        ctx.fail(f"账本事务失败（文件已换名，下轮增量自愈）: {e}")
        return
    if doubtful:
        ctx.fail(f"{len(doubtful)} 个交易日行数异常（doubtful），未入账，可确认入账")
        return
    ctx.succeed({"kind": "full", "fetched": len(tasks), "failed": len(failed)})


def _discard_staging(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)
    Path(staging_dir).mkdir(parents=True, exist_ok=True)


def _first_structurally_bad_file(staging_dir: Path) -> str | None:
    for p in sorted(Path(staging_dir).glob("*.parquet")):
        try:
            df = pl.read_parquet(p)
        except Exception:
            return p.name
        if not file_structure_ok(df):
            return p.name
    return None


def _staging_day_rows(staging_dir: Path) -> dict:
    files = sorted(Path(staging_dir).glob("*.parquet"))
    if not files:
        return {}
    s = (
        pl.scan_parquet([str(p) for p in files])
        .group_by("date").agg(pl.len().alias("n")).collect()
    )
    return {row["date"]: row["n"] for row in s.iter_rows(named=True)}


def _run_backfill_batch(ctx, pro, store, effective, codes, bars_dir,
                        bucket, progress, cancel_check) -> bool:
    """INV-3：永不触碰日账本。成功销账；失败仅记 code/unknown（INV-5）。"""
    tasks = []
    for code in codes:
        rng = effective.clamped_range(code)
        if rng is None:
            ctx.log(f"补齐跳过 {code}：不在有效清单")
            continue
        tasks.append((code, rng[0], rng[1]))
    if not tasks:
        ctx.log("补齐：无可拉代码")
        return True
    outcomes = run_backfill(pro, tasks, bars_dir,
                            bucket=bucket, progress=progress, cancel_check=cancel_check)
    ok = True
    for o in outcomes:
        if o.ok:
            store.clear_skip(o.code)
        else:
            ok = False
            if o.kind in (FailureKind.CODE, FailureKind.UNKNOWN):
                store.record_skip_failure(o.code, o.error, o.kind.value)
            ctx.log(f"补齐 {o.code} 失败（{o.kind.value if o.kind else '?'}）: {o.error}",
                    level="ERROR")
    ctx.log(f"补齐完成：{sum(1 for o in outcomes if o.ok)}/{len(outcomes)} 成功")
    return ok


def _tail_backfill(ctx, pro, store, effective, bars_dir, bucket, progress, cancel_check):
    """通道一：sync_skipped 非空即自动带一轮补齐；失败不翻转主作业终态（§3.7）。"""
    rows = store.skipped_rows()
    if not rows:
        return
    ctx.log(f"尾部补齐：重试 sync_skipped 中 {len(rows)} 只")
    if not _run_backfill_batch(ctx, pro, store, effective, [r["code"] for r in rows],
                               bars_dir, bucket, progress, cancel_check):
        ctx.log("尾部补齐存在失败（主作业终态不受影响）")


def backfill_codes_worker(ctx: JobContext, request: dict, now_cn: datetime | None = None) -> None:
    """通道二独立作业（job_type=market_backfill_codes，互斥集内）。"""
    from trendradar.infrastructure.runtime import storage_root

    root = storage_root()
    store = SyncStore(root)
    bars_dir = root / "market" / "bars"
    try:
        now_cn = now_cn or datetime.now(SHANGHAI)
        calendar = store.calendar_days()
        max_day = max(calendar) if calendar else None
        if max_day is None or max_day < now_cn.date():
            ctx.fail("官方日历未就绪/过期，拒绝补齐")
            return
        from trendradar.domain.market.sync.spec import latest_tradeable_day

        latest = latest_tradeable_day(calendar, now_cn)
        meta_file = root / "market" / "stock_meta.parquet"
        if meta_file.exists():
            meta = pl.read_parquet(meta_file)
        else:
            meta = sync_stock_list(bars_dir)
        effective = build_effective_list(meta, request.get("exclude_boards") or [], latest)
        codes = request.get("codes") or store.excluded_codes()
        bucket = TokenBucket()
        ok = _run_backfill_batch(
            ctx, get_pro(), store, effective, codes, bars_dir, bucket,
            lambda cur, total, msg: ctx.update_progress(cur, total, msg),
            lambda: ctx.check_cancelled(),
        )
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
        elif ok:
            ctx.succeed({"kind": "market_backfill_codes", "codes": len(codes)})
        else:
            ctx.fail("补齐存在失败（明细见日志）")
    except Exception as e:
        ctx.fail(f"意外错误: {e}")
    finally:
        _invalidate_status_cache()
