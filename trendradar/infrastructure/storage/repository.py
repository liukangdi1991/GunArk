from __future__ import annotations

import sqlite3
from typing import Any


class AppRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_execution(self, execution_key: str, execution_type: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
            (execution_key, execution_type),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_execution(self, execution_key: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM executions WHERE execution_key = ?", (execution_key,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def create_strategy_group(
        self, id: str, name: str, description: str = "", sort_order: int = 0
    ) -> None:
        self.conn.execute(
            "INSERT INTO strategy_groups (id, name, description, sort_order) VALUES (?, ?, ?, ?)",
            (id, name, description, sort_order),
        )
        self.conn.commit()

    def get_strategy_group(self, id: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM strategy_groups WHERE id = ?", (id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def create_job(self, job_id: str, job_type: str, request_json: str = "{}") -> int:
        cur = self.conn.execute(
            "INSERT INTO jobs (job_id, job_type, request_json) VALUES (?, ?, ?)",
            (job_id, job_type, request_json),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_job(self, job_id: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def create_artifact(
        self,
        execution_key: str,
        artifact_type: str,
        storage_key: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        checksum: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO artifacts (execution_key, artifact_type, storage_key, mime_type, size_bytes, checksum) VALUES (?, ?, ?, ?, ?, ?)",
            (execution_key, artifact_type, storage_key, mime_type, size_bytes, checksum),
        )
        self.conn.commit()
        return cur.lastrowid
