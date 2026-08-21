import time
from pathlib import Path

import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _executor(tmp_path: Path) -> JobExecutor:
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return JobExecutor(JobStore(sc.db_path))


def test_second_market_sync_rejected_while_running(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError, match="already running"):
            ex.submit("market_sync", lambda ctx: None, {})
    finally:
        ex._jobs[first].future.result(timeout=5)
        ex.shutdown(wait=True)


def test_other_job_types_not_blocked(tmp_path):
    ex = _executor(tmp_path)
    j1 = ex.submit("selection", lambda ctx: None, {})
    j2 = ex.submit("selection", lambda ctx: None, {})
    assert j1 != j2
    ex.shutdown(wait=True)
