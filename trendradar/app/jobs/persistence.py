from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _generate_job_id(job_type: str) -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{date_str}_{time_str}_{job_type}_{short_uuid}"


class JobStore:
    """Job metadata persistence over a single shared SQLite connection.

    The connection is shared across worker threads (check_same_thread=False),
    so every operation is serialized under one lock — concurrent execute/commit
    on the same connection corrupts transaction state and fails jobs.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._conn = conn
        return self._conn

    def create_job(self, job_type: str, request: dict) -> str:
        job_id = _generate_job_id(job_type)
        request_json = json.dumps(request, ensure_ascii=False)
        with self._lock:
            self._get_conn().execute(
                "INSERT INTO jobs (job_id, job_type, status, request_json) VALUES (?, ?, 'queued', ?)",
                (job_id, job_type, request_json),
            )
            self._get_conn().commit()
        return job_id

    def set_status(
        self,
        job_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._get_conn().execute(
                "UPDATE jobs SET status = ?, finished_at = ?, result_json = ?, error_message = ? WHERE job_id = ?",
                (
                    status,
                    now,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    job_id,
                ),
            )
            self._get_conn().commit()

    def append_log(
        self, job_id: str, sequence: int, level: str, message: str
    ) -> None:
        with self._lock:
            self._get_conn().execute(
                "INSERT OR IGNORE INTO job_logs (job_id, sequence, level, message) VALUES (?, ?, ?, ?)",
                (job_id, sequence, level, message),
            )
            self._get_conn().commit()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("request_json"):
            d["request"] = json.loads(d["request_json"])
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        return d

    def get_logs(self, job_id: str, offset: int = 0) -> list[str]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT message FROM job_logs WHERE job_id = ? ORDER BY sequence ASC",
                (job_id,),
            ).fetchall()
        messages = [r["message"] for r in rows]
        return messages[offset:]

    def update_started_at(self, job_id: str) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._get_conn().execute(
                "UPDATE jobs SET started_at = ?, status = 'running' WHERE job_id = ?",
                (now, job_id),
            )
            self._get_conn().commit()
