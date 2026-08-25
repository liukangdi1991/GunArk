from __future__ import annotations

import time
from pathlib import Path

import pytest
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _create_store(db_path: Path):
    from trendradar.app.jobs.persistence import JobStore

    return JobStore(db_path)


def _create_executor(store, max_workers=2):
    from trendradar.app.jobs.executor import JobExecutor

    return JobExecutor(store, max_workers=max_workers)


def _init_db(tmp_path: Path) -> Path:
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)
    conn.close()
    return sc.db_path


# --- JobStore tests ---


def test_job_store_create_job(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)

    job_id = store.create_job("selection", {"strategy": "test"})

    row = store.get_job(job_id)
    assert row is not None
    assert row["job_id"] == job_id
    assert row["job_type"] == "selection"
    assert row["status"] == "queued"
    assert row["created_at"] is not None


def test_job_store_set_status_to_success(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    job_id = store.create_job("backtest", {})

    store.set_status(job_id, "success", result={"trades": 5})

    row = store.get_job(job_id)
    assert row["status"] == "success"
    assert row["finished_at"] is not None
    assert row["result_json"] is not None


def test_job_store_set_status_to_failed(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    job_id = store.create_job("backtest", {})

    store.set_status(job_id, "failed", error="something went wrong")

    row = store.get_job(job_id)
    assert row["status"] == "failed"
    assert row["error_message"] == "something went wrong"
    assert row["finished_at"] is not None


def test_job_store_append_log(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    job_id = store.create_job("selection", {})

    store.append_log(job_id, 1, "INFO", "starting")
    store.append_log(job_id, 2, "WARNING", "something suspicious")

    logs = store.get_logs(job_id)
    assert len(logs) == 2
    assert "starting" in logs[0]
    assert "suspicious" in logs[1]


def test_job_store_get_logs_with_offset(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    job_id = store.create_job("selection", {})

    for i in range(5):
        store.append_log(job_id, i + 1, "INFO", f"log {i + 1}")

    logs = store.get_logs(job_id, offset=3)
    assert len(logs) == 2
    assert "log 4" in logs[0]


def test_job_store_get_nonexistent_job(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)

    assert store.get_job("nonexistent") is None


def test_job_store_get_empty_logs(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    job_id = store.create_job("selection", {})

    assert store.get_logs(job_id) == []


# --- JobExecutor tests ---


def test_submit_and_run_to_success(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    def work(ctx):
        ctx.log("working")
        ctx.succeed({"done": True})

    job_id = executor.submit("selection", work, {"param": 1})

    time.sleep(0.5)

    state = executor.get_state(job_id)
    assert state["status"] == "success"
    assert state["result"] == {"done": True}

    logs = executor.get_console(job_id)
    assert "working" in logs


def test_submit_and_cancel_before_finish(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    def slow_work(ctx):
        for _ in range(100):
            time.sleep(0.01)
            if ctx.check_cancelled():
                ctx.log("cancelled")
                return
        ctx.succeed({"done": True})

    job_id = executor.submit("backtest", slow_work, {})

    time.sleep(0.02)

    assert executor.cancel(job_id) is True

    time.sleep(0.5)

    state = executor.get_state(job_id)
    assert state["status"] in ("cancelled", "cancelling")


def test_job_logs_persisted_across_crash(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    def work(ctx):
        ctx.log("step 1")
        ctx.log("step 2")
        ctx.succeed({})

    job_id = executor.submit("selection", work, {})
    time.sleep(0.3)

    console = executor.get_console(job_id)
    assert "step 1" in console
    assert "step 2" in console


def test_progress_update_in_logs(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    def work(ctx):
        ctx.update_progress(50, 100, "half done")
        ctx.succeed({})

    job_id = executor.submit("selection", work, {})
    time.sleep(0.3)

    console = executor.get_console(job_id)
    assert "half done" in console


def test_two_jobs_run_concurrently(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    results = []

    def work_a(ctx):
        time.sleep(0.05)
        results.append("a")
        ctx.succeed({})

    def work_b(ctx):
        time.sleep(0.05)
        results.append("b")
        ctx.succeed({})

    job_a = executor.submit("selection", work_a, {})
    job_b = executor.submit("backtest", work_b, {})

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if len(results) == 2:
            break
        time.sleep(0.05)

    assert "a" in results
    assert "b" in results

    assert executor.get_state(job_a)["status"] == "success"
    assert executor.get_state(job_b)["status"] == "success"


def test_job_id_format(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)

    job_id = store.create_job("selection", {})

    parts = job_id.split("_")
    assert len(parts) == 4
    assert len(parts[0]) == 8
    assert len(parts[1]) == 6
    assert parts[2] == "selection"


def test_failed_job_records_error(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)

    def failing_work(ctx):
        ctx.log("starting")
        raise ValueError("boom")

    job_id = executor.submit("backtest", failing_work, {})

    time.sleep(0.3)

    state = executor.get_state(job_id)
    assert state["status"] == "failed"
    assert "boom" in state.get("error", "")

    console = executor.get_console(job_id)
    assert "starting" in console


def test_submit_after_executor_shutdown(tmp_path):
    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=1)
    executor.shutdown(wait=True)

    with pytest.raises(RuntimeError):
        executor.submit("selection", lambda ctx: None, {})


def test_concurrent_job_writes_are_thread_safe(tmp_path):
    """Regression: shared sqlite connection must not race on concurrent writes.

    Two workers finishing at the same moment previously could collide on the
    single connection's execute/commit and fail one job with a sqlite error.
    """
    import threading

    db_path = _init_db(tmp_path)
    store = _create_store(db_path)
    executor = _create_executor(store, max_workers=2)
    barrier = threading.Barrier(2)

    def work(ctx):
        barrier.wait(timeout=5)
        time.sleep(0.01)
        ctx.succeed({"x": 1})

    j1 = executor.submit("selection", work, {})
    j2 = executor.submit("backtest", work, {})

    # 用 store 侧查询等待完成（executor 完成后会清理内部注册表）
    deadline = time.time() + 10
    while time.time() < deadline:
        s1 = executor.get_state(j1)["status"]
        s2 = executor.get_state(j2)["status"]
        if s1 == "success" and s2 == "success":
            break
        time.sleep(0.02)

    assert executor.get_state(j1)["status"] == "success"
    assert executor.get_state(j2)["status"] == "success"
    executor.shutdown(wait=True)
