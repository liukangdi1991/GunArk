"""SQLite metadata registration for completed executions.

Spec: artifact metadata is only registered after the business artifacts are
fully written. These helpers are called by the service layer right after the
artifact files are persisted; a failure here surfaces as a failed job instead
of silently producing unregistered artifacts.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

_MIME_TYPES = {
    ".json": "application/json",
    ".parquet": "application/octet-stream",
    ".log": "text/plain",
}


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _storage_key(storage_root: Path, path: Path) -> str:
    return path.relative_to(storage_root).as_posix()


def register_execution(
    conn: sqlite3.Connection,
    execution_key: str,
    execution_type: str,
    manifest_key: str | None = None,
    lineage_key: str | None = None,
) -> None:
    """Insert an executions row (idempotent per execution_key)."""
    conn.execute(
        "INSERT OR IGNORE INTO executions "
        "(execution_key, execution_type, status, manifest_key, lineage_key) "
        "VALUES (?, ?, 'success', ?, ?)",
        (execution_key, execution_type, manifest_key, lineage_key),
    )


def register_artifacts(
    conn: sqlite3.Connection,
    execution_key: str,
    artifact_prefix: str,
    files: list[Path],
    storage_root: Path,
) -> None:
    """Register artifact rows for the given written files.

    artifact_prefix is the execution subdirectory, e.g. 'selection' or
    'backtest'; the artifact_type column becomes '<prefix>/<filename>'.
    storage_key is the path relative to the storage root.
    """
    for path in files:
        if not path.is_file():
            continue
        artifact_type = f"{artifact_prefix}/{path.name}"
        conn.execute(
            "INSERT OR IGNORE INTO artifacts "
            "(execution_key, artifact_type, storage_key, mime_type, size_bytes, checksum) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                execution_key,
                artifact_type,
                _storage_key(storage_root, path),
                _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                path.stat().st_size,
                _checksum(path),
            ),
        )
