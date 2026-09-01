from datetime import date

import pytest

from trendradar.app.services.market_sync.commit import commit_full, commit_incremental
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore


@pytest.fixture()
def store(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return SyncStore(tmp_path)


def test_commit_incremental_adds_claimed_and_persists_doubtful(store):
    commit_incremental(store, claimed_days=[date(2026, 8, 26)],
                       doubtful_days=[date(2026, 8, 25)])
    assert store.done_days() == {date(2026, 8, 26)}
    assert store.doubtful_days() == [date(2026, 8, 25)]
    assert store.ledger_suspect() is False


def test_commit_incremental_atomic_on_failure(store):
    from contextlib import contextmanager

    class BoomStore(SyncStore):
        @contextmanager
        def transaction(self):
            with self._sc.connection() as conn:
                try:
                    yield conn
                    raise RuntimeError("boom")  # 提交前爆炸 → 整体回滚
                except Exception:
                    conn.rollback()
                    raise

    boom = BoomStore(store._sc.storage_root)
    with pytest.raises(RuntimeError):
        commit_incremental(boom, claimed_days=[date(2026, 8, 26)], doubtful_days=[])
    assert store.done_days() == set()  # 事务回滚，整体不动


def test_commit_full_replaces_and_clears_suspect(store):
    store.add_done_days([date(2015, 1, 2)])      # 旧账
    store.set_ledger_suspect(True)
    commit_full(store, covered_days=[date(2015, 1, 5), date(2015, 1, 6)],
                doubtful_days=[date(2015, 7, 8)])
    assert store.done_days() == {date(2015, 1, 5), date(2015, 1, 6)}
    assert store.ledger_suspect() is False        # 一次全量成功后清除
    assert store.doubtful_days() == [date(2015, 7, 8)]
    assert store.get_meta("last_full_success_at") is not None
