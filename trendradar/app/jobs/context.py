from __future__ import annotations

from collections.abc import Callable

from trendradar.app.jobs.persistence import JobStore


class JobContext:
    def __init__(
        self,
        job_id: str,
        job_type: str,
        store: JobStore,
        cancel_check: Callable[[], bool],
    ) -> None:
        self.job_id = job_id
        self.job_type = job_type
        self._store = store
        self._cancel_check = cancel_check
        self._seq = 0

    def log(self, message: str, level: str = "INFO") -> None:
        self._seq += 1
        self._store.append_log(self.job_id, self._seq, level, message)

    def update_progress(
        self, current: int, total: int, message: str = ""
    ) -> None:
        pct = int(current / total * 100) if total > 0 else 0
        progress_msg = f"[PROGRESS] {current}/{total} ({pct}%)"
        if message:
            progress_msg += f" {message}"
        self.log(progress_msg, level="INFO")

    def check_cancelled(self) -> bool:
        return self._cancel_check()

    def succeed(self, result: dict) -> None:
        self._store.set_status(self.job_id, "success", result=result)

    def fail(self, error: str) -> None:
        self._store.set_status(self.job_id, "failed", error=error)
