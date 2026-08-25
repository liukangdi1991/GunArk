from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from trendradar.app.jobs.context import JobContext
from trendradar.app.jobs.persistence import JobStore


class JobState:
    def __init__(self, job_id: str, job_type: str, future: Future) -> None:
        self.job_id = job_id
        self.job_type = job_type
        self.future = future


class JobExecutor:
    def __init__(self, store: JobStore, max_workers: int = 2) -> None:
        self._store = store
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, JobState] = {}
        self._cancelling: set[str] = set()
        self._lock = Lock()
        self._shutdown = False

    def submit(
        self,
        job_type: str,
        run_fn: Callable[[JobContext], None],
        request: dict,
    ) -> str:
        if self._shutdown:
            raise RuntimeError("Executor has been shut down")

        if job_type == "market_sync":
            # 检查 + 注册必须在同一把锁内，否则并发提交会双跑（TOCTOU）
            with self._lock:
                if any(
                    j.job_type == "market_sync" and not j.future.done()
                    for j in self._jobs.values()
                ):
                    raise RuntimeError("market_sync job already running")
                job_id = self._store.create_job(job_type, request)
                fut = self._pool.submit(self._run, job_id, job_type, run_fn)
                self._jobs[job_id] = JobState(job_id, job_type, fut)
                return job_id

        job_id = self._store.create_job(job_type, request)
        fut = self._pool.submit(self._run, job_id, job_type, run_fn)
        with self._lock:
            self._jobs[job_id] = JobState(job_id, job_type, fut)
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            if job_id in self._cancelling:
                return False
            state = self._jobs[job_id]
            if state.future.done():
                return False  # 已完成任务不可取消
            self._cancelling.add(job_id)
        return True

    def get_state(self, job_id: str) -> dict:
        job = self._store.get_job(job_id)
        if job is None:
            return {"job_id": job_id, "status": "unknown"}
        return {
            "job_id": job["job_id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "request": job.get("request"),
            "result": job.get("result"),
            "error": job.get("error_message"),
        }

    def get_console(self, job_id: str, offset: int = 0) -> str:
        logs = self._store.get_logs(job_id, offset=offset)
        return "\n".join(logs)

    def shutdown(self, wait: bool = True) -> None:
        self._shutdown = True
        self._pool.shutdown(wait=wait)

    def _get_job_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelling

    def _run(
        self,
        job_id: str,
        job_type: str,
        run_fn: Callable[[JobContext], None],
    ) -> None:
        ctx = JobContext(
            job_id=job_id,
            job_type=job_type,
            store=self._store,
            cancel_check=lambda: self._is_cancelled(job_id),
        )
        try:
            self._store.update_started_at(job_id)
        except Exception:
            pass
        try:
            run_fn(ctx)
            if self._is_cancelled(job_id):
                self._store.set_status(job_id, "cancelled")
            else:
                job = self._store.get_job(job_id)
                if job is not None and job["status"] == "running":
                    ctx.succeed({})
        except Exception as e:
            if self._is_cancelled(job_id):
                self._store.set_status(job_id, "cancelled")
            else:
                ctx.fail(str(e))
        finally:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._cancelling.discard(job_id)
