"""账本四表读写 + 单事务提交（唯一有权写账本的模块）。见 spec §3.1/§5。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from trendradar.infrastructure.storage.connection import StorageConnection


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class SyncStore:
    def __init__(self, storage_root: Path) -> None:
        self._sc = StorageConnection(Path(storage_root))

    @contextmanager
    def transaction(self):
        with self._sc.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---- trade_calendar（INV-2：只增不减）----

    def insert_calendar_days(self, days) -> None:
        rows = [(d.isoformat(),) for d in sorted(set(days))]
        if not rows:
            return
        with self._sc.connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES (?)", rows
            )
            conn.commit()

    def calendar_days(self) -> set[date]:
        with self._sc.connection() as conn:
            rows = conn.execute("SELECT trade_date FROM trade_calendar").fetchall()
        return {date.fromisoformat(r["trade_date"]) for r in rows}

    def max_calendar_day(self) -> date | None:
        with self._sc.connection() as conn:
            row = conn.execute("SELECT MAX(trade_date) AS m FROM trade_calendar").fetchone()
        return date.fromisoformat(row["m"]) if row and row["m"] else None

    # ---- sync_done_days ----

    def done_days(self) -> set[date]:
        with self._sc.connection() as conn:
            rows = conn.execute("SELECT trade_date FROM sync_done_days").fetchall()
        return {date.fromisoformat(r["trade_date"]) for r in rows}

    def add_done_days(self, days) -> None:
        rows = [(d.isoformat(), _now_iso()) for d in sorted(set(days))]
        if not rows:
            return
        with self._sc.connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
                rows,
            )
            conn.commit()

    def replace_done_days(self, days: set[date]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sync_done_days")
            conn.executemany(
                "INSERT INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
                [(d.isoformat(), _now_iso()) for d in sorted(days)],
            )

    # ---- sync_meta ----

    def get_meta(self, key: str) -> str | None:
        with self._sc.connection() as conn:
            row = conn.execute(
                "SELECT value FROM sync_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._sc.connection() as conn:
            conn.execute(
                "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def ledger_suspect(self) -> bool:
        return self.get_meta("ledger_suspect") == "1"

    def set_ledger_suspect(self, flag: bool) -> None:
        self.set_meta("ledger_suspect", "1" if flag else "0")

    def doubtful_days(self) -> list[date]:
        raw = self.get_meta("doubtful_days")
        if not raw:
            return []
        return [date.fromisoformat(d) for d in json.loads(raw)]

    def set_doubtful_days(self, days) -> None:
        self.set_meta(
            "doubtful_days", json.dumps(sorted(d.isoformat() for d in days))
        )

    def set_last_full_success(self) -> None:
        self.set_meta("last_full_success_at", _now_iso())

    # ---- sync_skipped ----

    def skipped_rows(self) -> list[dict]:
        with self._sc.connection() as conn:
            rows = conn.execute(
                "SELECT code, attempts, last_error, first_seen, last_attempt, kind "
                "FROM sync_skipped ORDER BY code"
            ).fetchall()
        return [dict(r) for r in rows]

    def excluded_codes(self) -> list[str]:
        with self._sc.connection() as conn:
            rows = conn.execute(
                "SELECT code FROM sync_skipped WHERE attempts >= 3 ORDER BY code"
            ).fetchall()
        return [r["code"] for r in rows]

    def record_skip_failure(self, code: str, error: str | None, kind: str) -> None:
        now = _now_iso()
        with self._sc.connection() as conn:
            conn.execute(
                "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
                "VALUES (?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET attempts = attempts + 1, "
                "last_error = excluded.last_error, last_attempt = excluded.last_attempt, "
                "kind = excluded.kind",
                (code, error, now, now, kind),
            )
            conn.commit()

    def clear_skip(self, code: str) -> None:
        with self._sc.connection() as conn:
            conn.execute("DELETE FROM sync_skipped WHERE code = ?", (code,))
            conn.commit()

    def reset_skip_attempts(self) -> None:
        with self._sc.connection() as conn:
            conn.execute("UPDATE sync_skipped SET attempts = 0")
            conn.commit()
