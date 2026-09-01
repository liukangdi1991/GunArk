import time

import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _executor(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return JobExecutor(JobStore(sc.db_path))


def test_second_bars_sync_rejected_while_running(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError):
            ex.submit("market_bars_sync", lambda ctx: None, {})
    finally:
        ex._jobs[first].future.result(timeout=5)
        ex.shutdown(wait=True)


def test_bars_sync_and_backfill_mutually_exclusive(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError):
            ex.submit("market_backfill_codes", lambda ctx: None, {})
        with pytest.raises(RuntimeError):
            ex.submit("market_bars_sync", lambda ctx: None, {})
    finally:
        ex._jobs[first].future.result(timeout=5)
        ex.shutdown(wait=True)


def test_other_job_types_not_excluded(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    job_id = ex.submit("selection", lambda ctx: None, {})
    try:
        assert job_id
    finally:
        ex._jobs[first].future.result(timeout=5)
        ex.shutdown(wait=True)


def test_worker_terminal_state_not_overwritten_on_cancel(tmp_path):
    """spec §3.9 坑 2：worker 已写 failed('Cancelled by user') 则不覆写为 cancelled。"""
    ex = _executor(tmp_path)

    def worker(ctx):
        time.sleep(0.5)          # 让 cancel 先登记，再写终态
        ctx.fail("Cancelled by user")

    job_id = ex.submit("market_bars_sync", worker, {})
    fut = ex._jobs[job_id].future
    assert ex.cancel(job_id)
    fut.result(timeout=5)
    try:
        state = ex.get_state(job_id)
        assert state["status"] == "failed"
        assert state["error"] == "Cancelled by user"
    finally:
        ex.shutdown(wait=True)


def test_plain_cancel_still_yields_cancelled(tmp_path):
    ex = _executor(tmp_path)
    job_id = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    fut = ex._jobs[job_id].future
    assert ex.cancel(job_id)
    fut.result(timeout=5)
    try:
        assert ex.get_state(job_id)["status"] == "cancelled"
    finally:
        ex.shutdown(wait=True)
