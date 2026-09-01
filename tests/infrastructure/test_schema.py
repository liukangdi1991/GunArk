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
        "sync_done_days",
        "sync_meta",
        "sync_skipped",
        "trade_calendar",
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


def test_calendar_insert_or_ignore(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)
    conn.execute("INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES ('2026-08-27')")
    conn.execute("INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES ('2026-08-27')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS c FROM trade_calendar").fetchone()["c"] == 1


def test_sync_skipped_upsert_conflict(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)
    conn.execute(
        "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
        "VALUES ('000001', 1, 'e1', '2026-08-27', '2026-08-27', 'code')"
    )
    conn.execute(
        "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
        "VALUES ('000001', 1, 'e2', '2026-08-28', '2026-08-28', 'code') "
        "ON CONFLICT(code) DO UPDATE SET attempts = attempts + 1, "
        "last_error = excluded.last_error, last_attempt = excluded.last_attempt"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sync_skipped WHERE code = '000001'").fetchone()
    assert row["attempts"] == 2
    assert row["last_error"] == "e2"
    assert row["first_seen"] == "2026-08-27"
