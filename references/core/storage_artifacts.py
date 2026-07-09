from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from core.storage_utils import execution_type_from_legacy, sha256


class ArtifactRepository:
    """Artifact persistence and filesystem deletion operations."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def register_artifact(
        self,
        *,
        execution_key: str,
        artifact_type: str,
        path: Path,
        mime_type: str,
        run_type: str = "backtest",
    ) -> None:
        self.storage.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        stat = path.stat()
        with self.storage._connect() as conn:
            execution_id = self.storage._get_execution_id(conn, execution_key)
            if execution_id is None:
                execution_id = self.storage._upsert_execution(
                    conn,
                    execution_key=execution_key,
                    execution_type=execution_type_from_legacy(run_type),
                    status="success",
                    created_at=now,
                    finished_at=now,
                    object_dir_key=self.storage._execution_object_key(path.parent),
                )
            conn.execute(
                """
                insert into artifacts (
                    execution_id,
                    artifact_scope,
                    artifact_type,
                    storage_key,
                    mime_type,
                    size_bytes,
                    checksum,
                    created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(execution_id, artifact_scope, artifact_type) do update set
                    storage_key = excluded.storage_key,
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    checksum = excluded.checksum,
                    created_at = excluded.created_at
                """,
                (
                    execution_id,
                    str(run_type),
                    artifact_type,
                    self.storage.storage_key(path),
                    mime_type,
                    int(stat.st_size),
                    sha256(path),
                    now,
                ),
            )

    def list_artifacts(self, execution_key: str, run_type: str | None = None) -> list[dict[str, Any]]:
        self.storage.ensure_ready()
        with self.storage._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    a.artifact_scope,
                    a.artifact_type,
                    a.storage_key,
                    a.mime_type,
                    a.size_bytes,
                    a.checksum,
                    a.created_at
                from artifacts a
                join executions er on er.id = a.execution_id
                where er.execution_key = ?
                    and (? is null or a.artifact_scope = ?)
                order by a.artifact_type
                """,
                (execution_key, run_type, run_type),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_storage_key(self, storage_key: str) -> dict[str, str] | None:
        if not storage_key:
            return None
        try:
            root = self.storage.objects_root.resolve()
            path = self.storage.artifact_path(storage_key).resolve()
            path.relative_to(root)
        except ValueError:
            return {"storage_key": storage_key, "error": "storage key 超出 objects 目录，已拒绝删除"}

        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            return {"storage_key": storage_key, "error": str(exc)}
        return None
