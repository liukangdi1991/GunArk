from __future__ import annotations

import sqlite3
from pathlib import Path


class StorageConnection:
    """Connection and path helpers for the app storage root."""

    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root)
        self.db_path = self.storage_root / "app.db"
        self.objects_root = self.storage_root / "objects"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("pragma foreign_keys = on")
        return conn

    def artifact_path(self, storage_key: str) -> Path:
        path = Path(storage_key)
        return path if path.is_absolute() else self.objects_root / path

    def storage_key(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.objects_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def execution_object_key(self, object_dir: Path) -> str:
        key = self.storage_key(object_dir)
        parts = key.split("/")
        if len(parts) >= 2 and parts[0] == "executions":
            return "/".join(parts[:2])
        return key

