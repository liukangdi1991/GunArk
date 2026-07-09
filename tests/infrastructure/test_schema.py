import pytest
import sqlite3
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.connection import StorageConnection


def test_schema_creates_all_tables(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()} - {"sqlite_sequence"}
    expected = {
        "artifacts",
        "execution_items",
        "execution_links",
        "executions",
        "job_logs",
        "jobs",
        "market_sync_runs",
        "strategy_group_members",
        "strategy_groups",
        "strategy_settings",
    }
    assert tables == expected


def test_foreign_keys_enforced(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'selection')"
    )
    conn.execute(
        "INSERT INTO execution_items (execution_key, item_type, item_key) VALUES ('k1', 'test', 't1')"
    )
    conn.commit()

    with_conn = sc.connect()
    with_conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        with_conn.execute(
            "INSERT INTO execution_items (execution_key, item_type, item_key) VALUES ('nonexistent', 'test', 't2')"
        )


def test_unique_execution_key(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)

    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'selection')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_key, execution_type) VALUES ('k1', 'backtest')"
        )
