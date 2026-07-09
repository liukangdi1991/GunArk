from __future__ import annotations

from datetime import datetime
from typing import Any


class ExecutionLogRepository:
    """Links async job logs to resulting business resources."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def record_execution_log_link(
        self,
        *,
        job_execution_id: str,
        resource_type: str,
        resource_execution_key: str | None,
        resource_url: str | None,
    ) -> None:
        self.storage.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        with self.storage._connect() as conn:
            self.storage._upsert_execution_log_link(
                conn,
                job_execution_id=job_execution_id,
                resource_type=resource_type,
                resource_execution_key=resource_execution_key,
                resource_url=resource_url,
                created_at=now,
            )
