from datetime import date

import pytest

from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore


@pytest.fixture()
def store(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return SyncStore(tmp_path)


def test_calendar_insert_and_read(store):
    store.insert_calendar_days([date(2026, 8, 26), date(2026, 8, 27)])
    store.insert_calendar_days([date(2026, 8, 27), date(2026, 8, 28)])  # 幂等
    assert store.calendar_days() == {date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28)}
    assert store.max_calendar_day() == date(2026, 8, 28)


def test_calendar_empty(store):
    assert store.calendar_days() == set()
    assert store.max_calendar_day() is None


def test_done_days_add_and_replace(store):
    store.add_done_days([date(2026, 8, 26), date(2026, 8, 27)])
    assert store.done_days() == {date(2026, 8, 26), date(2026, 8, 27)}
    store.replace_done_days({date(2026, 8, 27)})
    assert store.done_days() == {date(2026, 8, 27)}


def test_meta_ledger_suspect_default_false(store):
    assert store.ledger_suspect() is False
    store.set_ledger_suspect(True)
    assert store.ledger_suspect() is True
    store.set_ledger_suspect(False)
    assert store.ledger_suspect() is False


def test_meta_doubtful_days_roundtrip(store):
    assert store.doubtful_days() == []
    store.set_doubtful_days([date(2015, 7, 8), date(2015, 7, 9)])
    assert store.doubtful_days() == [date(2015, 7, 8), date(2015, 7, 9)]
    store.set_doubtful_days([])
    assert store.doubtful_days() == []


def test_last_full_success_at(store):
    assert store.get_meta("last_full_success_at") is None
    store.set_last_full_success()
    assert store.get_meta("last_full_success_at") is not None


def test_skipped_record_and_excluded(store):
    assert store.skipped_rows() == []
    assert store.excluded_codes() == []
    store.record_skip_failure("000001", "参数错误", "code")
    store.record_skip_failure("000001", "参数错误2", "code")
    store.record_skip_failure("000001", "参数错误3", "code")
    rows = store.skipped_rows()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 3
    assert rows[0]["last_error"] == "参数错误3"
    assert store.excluded_codes() == ["000001"]  # attempts >= 3 才出列
    store.record_skip_failure("000002", "x", "unknown")
    assert store.excluded_codes() == ["000001"]  # attempts=1 不出列


def test_skipped_success_clears(store):
    store.record_skip_failure("000001", "e", "code")
    store.clear_skip("000001")
    assert store.skipped_rows() == []


def test_skipped_reset_attempts(store):
    store.record_skip_failure("000001", "e", "code")
    store.record_skip_failure("000001", "e", "code")
    store.record_skip_failure("000001", "e", "code")
    store.reset_skip_attempts()
    rows = store.skipped_rows()
    assert rows[0]["attempts"] == 0
    assert store.excluded_codes() == []


def test_transaction_rollback_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO sync_done_days (trade_date, synced_at) VALUES ('2026-08-26', 'x')"
            )
            raise RuntimeError("boom")
    assert store.done_days() == set()


def test_transaction_commits_on_success(store):
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO sync_done_days (trade_date, synced_at) VALUES ('2026-08-26', 'x')"
        )
    assert store.done_days() == {date(2026, 8, 26)}
