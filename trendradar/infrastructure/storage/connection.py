import sqlite3
from pathlib import Path


class StorageConnection:
    def __init__(self, storage_root: Path):
        self.storage_root = storage_root
        self.db_path = storage_root / "app.db"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
