import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


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

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()
