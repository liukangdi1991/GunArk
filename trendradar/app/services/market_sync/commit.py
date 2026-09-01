"""落账：单事务写账本（spec §3.1 "临时副本 → 写盘 → 读回校验 → 单事务落账" 的最后一步）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from trendradar.infrastructure.storage.sync_store import SyncStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _dump_dates(days) -> str:
    return json.dumps(sorted(set(d.isoformat() for d in days)))


def commit_incremental(
    store: SyncStore,
    claimed_days: list[date],
    doubtful_days: list[date],
) -> None:
    """增量批：单事务写声称日 + 更新 doubtful。失败则整体回滚。"""
    now = _now_iso()
    with store.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
            [(d.isoformat(), now) for d in sorted(set(claimed_days))],
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('doubtful_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_dump_dates(doubtful_days),),
        )


def commit_full(
    store: SyncStore,
    covered_days: list[date],
    doubtful_days: list[date],
) -> None:
    """全量批：单事务用覆盖区间（− doubtful）替换 sync_done_days，
    清 ledger_suspect、记 last_full_success_at（spec §3.4/§3.6）。"""
    now = _now_iso()
    with store.transaction() as conn:
        conn.execute("DELETE FROM sync_done_days")
        conn.executemany(
            "INSERT INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
            [(d.isoformat(), now) for d in sorted(set(covered_days))],
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('doubtful_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_dump_dates(doubtful_days),),
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('ledger_suspect', '0') "
            "ON CONFLICT(key) DO UPDATE SET value = '0'"
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('last_full_success_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now,),
        )
