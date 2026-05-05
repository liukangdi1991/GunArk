from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.storage_models import ExecutionType
from core.storage_utils import normalize_execution_keys


def ensure_artifacts_scope_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("pragma table_info(artifacts)").fetchall()}
    if "artifact_scope" in columns:
        return

    conn.execute("alter table artifacts rename to artifacts_old")
    conn.execute(
        """
        create table artifacts (
            id integer primary key autoincrement,
            execution_id integer not null,
            artifact_scope text not null,
            artifact_type text not null,
            storage_key text not null,
            mime_type text,
            size_bytes integer,
            checksum text,
            created_at text not null,
            foreign key (execution_id) references executions(id) on delete cascade,
            unique (execution_id, artifact_scope, artifact_type)
        )
        """
    )

    old_columns = {row[1] for row in conn.execute("pragma table_info(artifacts_old)").fetchall()}
    if "execution_id" in old_columns:
        artifact_scope_expr = (
            "run_type"
            if "run_type" in old_columns
            else """
            case
                when artifact_type in ('signals_json', 'picks_parquet') then 'selection'
                else 'backtest'
            end
            """
        )
        conn.execute(
            f"""
            insert or ignore into artifacts (
                id,
                execution_id,
                artifact_scope,
                artifact_type,
                storage_key,
                mime_type,
                size_bytes,
                checksum,
                created_at
            )
            select
                id,
                execution_id,
                {artifact_scope_expr},
                artifact_type,
                storage_key,
                mime_type,
                size_bytes,
                checksum,
                created_at
            from artifacts_old
            """
        )
    elif "run_id" in old_columns:
        artifact_scope_expr = "ao.run_type" if "run_type" in old_columns else "'backtest'"
        conn.execute(
            f"""
            insert or ignore into artifacts (
                id,
                execution_id,
                artifact_scope,
                artifact_type,
                storage_key,
                mime_type,
                size_bytes,
                checksum,
                created_at
            )
            select
                ao.id,
                er.id,
                {artifact_scope_expr},
                ao.artifact_type,
                ao.storage_key,
                ao.mime_type,
                ao.size_bytes,
                ao.checksum,
                ao.created_at
            from artifacts_old ao
            join executions er on er.execution_key = ao.run_id
            """
        )
    conn.execute("drop table artifacts_old")


def backfill_backtest_selection_links(storage: Any, conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select
            er.id as execution_id,
            er.execution_key,
            er.created_at,
            er.object_dir_key,
            br.start_date,
            br.end_date,
            br.signal_dir
        from executions er
        join backtest_results br on br.execution_id = er.id
        order by er.id
        """
    ).fetchall()
    for row in rows:
        execution_id = int(row["execution_id"])
        existing = conn.execute(
            "select 1 from backtest_selection_links where backtest_execution_id = ? limit 1",
            (execution_id,),
        ).fetchone()
        if existing is not None:
            continue
        selection_keys = selection_keys_from_backtest_meta(
            storage,
            conn,
            execution_id,
            str(row["object_dir_key"] or ""),
        )
        if not selection_keys:
            selection_keys = selection_keys_from_backtest_job_state(storage, str(row["execution_key"] or ""))
        if selection_keys:
            storage._replace_backtest_selection_links(conn, execution_id, selection_keys)


def backfill_execution_log_links(storage: Any, conn: sqlite3.Connection) -> None:
    jobs_root = storage.objects_root / "jobs"
    if not jobs_root.exists():
        return
    for state_path in sorted(jobs_root.glob("*/state.json")):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        job_execution_id = str(payload.get("execution_id") or "").strip()
        resource_type = str(payload.get("resource_type") or "").strip()
        resource_id = payload.get("resource_id")
        result_url = payload.get("result_url")
        if not job_execution_id or not resource_type:
            continue
        storage._upsert_execution_log_link(
            conn,
            job_execution_id=job_execution_id,
            resource_type=resource_type,
            resource_execution_key=str(resource_id).strip() if resource_id else None,
            resource_url=str(result_url).strip() if result_url else None,
            created_at=str(payload.get("finished_at") or payload.get("updated_at") or payload.get("created_at") or ""),
        )


def selection_keys_from_backtest_meta(
    storage: Any,
    conn: sqlite3.Connection,
    backtest_execution_id: int,
    object_dir_key: str,
) -> list[str]:
    paths: list[Path] = []
    artifact = conn.execute(
        """
        select storage_key
        from artifacts
        where execution_id = ?
            and artifact_scope = ?
            and artifact_type = ?
        limit 1
        """,
        (backtest_execution_id, ExecutionType.BACKTEST.value, "meta_json"),
    ).fetchone()
    if artifact is not None and artifact["storage_key"]:
        paths.append(storage.artifact_path(str(artifact["storage_key"])))

    clean_object_key = object_dir_key.strip("/")
    if clean_object_key:
        root = storage.objects_root / clean_object_key
        paths.extend([root / "backtest" / "meta.json", root / "meta.json"])

    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        keys = normalize_execution_keys([
            str(item) for item in (payload.get("selection_execution_keys") or [])
        ])
        if keys:
            return keys
    return []


def selection_keys_from_backtest_job_state(storage: Any, execution_key: str) -> list[str]:
    if not execution_key:
        return []
    jobs_root = storage.objects_root / "jobs"
    if not jobs_root.exists():
        return []

    for state_path in sorted(jobs_root.glob("*/state.json")):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("execution_key") != execution_key and payload.get("resource_id") != execution_key:
            continue
        return normalize_execution_keys([
            str(item) for item in (result.get("selection_execution_keys") or [])
        ]) or []
    return []
