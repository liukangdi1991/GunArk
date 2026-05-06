"""SQLite storage layer for selection/backtest executions and artifacts."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from core.storage_artifacts import ArtifactRepository
from core.storage_backtest import BacktestRepository
from core.storage_connection import StorageConnection
from core.storage_execution_logs import ExecutionLogRepository
from core.storage_migrations import (
    backfill_backtest_selection_links,
    backfill_execution_log_links,
    ensure_artifacts_scope_schema,
)
from core.storage_models import ExecutionItemType, ExecutionType, SelectionResultInUseError
from core.storage_records import StorageRecordMapper
from core.storage_schema import SCHEMA_SQL
from core.storage_selection import SelectionRepository
from core.storage_utils import (
    dedupe_storage_keys,
    delete_result,
    ensure_execution_item_type,
    ensure_execution_type,
    normalize_execution_keys,
    placeholders as make_placeholders,
    scoped_dir_key,
    serialize_value,
)


class AppStorage:
    def __init__(self, storage_root: Path | str = "storage") -> None:
        self.storage_root = Path(storage_root)
        self.db_path = self.storage_root / "app.db"
        self.objects_root = self.storage_root / "objects"
        self._connection = StorageConnection(self.storage_root)
        self._artifacts = ArtifactRepository(self)
        self._selection = SelectionRepository(self)
        self._backtest = BacktestRepository(self)
        self._execution_logs = ExecutionLogRepository(self)
        self._records = StorageRecordMapper(self)
        self._links_backfilled = False

    def ensure_ready(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            ensure_artifacts_scope_schema(conn)
            if not self._links_backfilled:
                backfill_backtest_selection_links(self, conn)
                backfill_execution_log_links(self, conn)
                self._links_backfilled = True

    def record_selection_result(
        self,
        *,
        execution_key: str,
        selection_date: str,
        strategies: list[str],
        strategy_snapshots: list[dict[str, Any]],
        data_dir: str,
        signal_file: str,
        summaries: list[dict[str, Any]],
        object_dir: Path,
        status: str = "success",
    ) -> None:
        self._selection.record_selection_result(
            execution_key=execution_key,
            selection_date=selection_date,
            strategies=strategies,
            strategy_snapshots=strategy_snapshots,
            data_dir=data_dir,
            signal_file=signal_file,
            summaries=summaries,
            object_dir=object_dir,
            status=status,
        )

    def record_backtest_result(
        self,
        *,
        execution_key: str,
        meta: Mapping[str, Any],
        summaries: list[dict[str, Any]],
        object_dir: Path,
        status: str = "success",
    ) -> None:
        self._backtest.record_backtest_result(
            execution_key=execution_key,
            meta=meta,
            summaries=summaries,
            object_dir=object_dir,
            status=status,
        )

    def register_artifact(
        self,
        *,
        execution_key: str,
        artifact_type: str,
        path: Path,
        mime_type: str,
        run_type: str = "backtest",
    ) -> None:
        self._artifacts.register_artifact(
            execution_key=execution_key,
            artifact_type=artifact_type,
            path=path,
            mime_type=mime_type,
            run_type=run_type,
        )

    def record_execution_log_link(
        self,
        *,
        job_execution_id: str,
        resource_type: str,
        resource_execution_key: str | None,
        resource_url: str | None,
    ) -> None:
        self._execution_logs.record_execution_log_link(
            job_execution_id=job_execution_id,
            resource_type=resource_type,
            resource_execution_key=resource_execution_key,
            resource_url=resource_url,
        )

    def list_selection_results(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._selection.list_selection_results(limit=limit)

    def get_selection_result(self, execution_key: str) -> dict[str, Any] | None:
        return self._selection.get_selection_result(execution_key)

    def list_backtest_results(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._backtest.list_backtest_results(limit=limit)

    def get_backtest_result(self, execution_key: str) -> dict[str, Any] | None:
        return self._backtest.get_backtest_result(execution_key)

    def list_artifacts(self, execution_key: str, run_type: str | None = None) -> list[dict[str, Any]]:
        return self._artifacts.list_artifacts(execution_key, run_type=run_type)

    def delete_backtest_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self._backtest.delete_backtest_results(execution_keys)

    def delete_selection_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self._selection.delete_selection_results(execution_keys)

    def selection_delete_blockers(self, execution_keys: list[str] | None = None) -> list[dict[str, Any]]:
        return self._selection.selection_delete_blockers(execution_keys)

    def artifact_path(self, storage_key: str) -> Path:
        return self._connection.artifact_path(storage_key)

    def storage_key(self, path: Path) -> str:
        return self._connection.storage_key(path)

    def _upsert_execution(
        self,
        conn: sqlite3.Connection,
        *,
        execution_key: str,
        execution_type: ExecutionType,
        status: str,
        created_at: str,
        finished_at: str,
        object_dir_key: str,
    ) -> int:
        execution_type = ensure_execution_type(execution_type)
        existing = conn.execute(
            "select id, execution_type, created_at from executions where execution_key = ?",
            (execution_key,),
        ).fetchone()
        if existing is not None:
            existing_type = ensure_execution_type(str(existing[1]))
            if existing_type != execution_type:
                execution_type = ExecutionType.SELECTION_BACKTEST
            created_at = str(existing[2] or created_at)
        conn.execute(
            """
            insert into executions (
                execution_key,
                execution_type,
                status,
                created_at,
                finished_at,
                object_dir_key
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(execution_key) do update set
                execution_type = excluded.execution_type,
                status = excluded.status,
                finished_at = excluded.finished_at,
                object_dir_key = excluded.object_dir_key
            """,
            (execution_key, execution_type.value, status, created_at, finished_at, object_dir_key),
        )
        existing_id = self._get_execution_id(conn, execution_key)
        if existing_id is None:
            raise RuntimeError(f"execution 写入失败: {execution_key}")
        return existing_id

    def _get_execution_id(self, conn: sqlite3.Connection, execution_key: str) -> int | None:
        row = conn.execute("select id from executions where execution_key = ?", (execution_key,)).fetchone()
        return None if row is None else int(row[0])

    def _upsert_execution_item(
        self,
        conn: sqlite3.Connection,
        *,
        execution_id: int,
        item_type: ExecutionItemType,
        item_key: str,
        item_name: str,
        item_class: str,
        description: str,
    ) -> int:
        item_type = ensure_execution_item_type(item_type)
        clean_key = item_key.strip()
        if not clean_key:
            raise ValueError("execution item key 不能为空")
        conn.execute(
            """
            insert into execution_items (
                execution_id,
                item_type,
                item_key,
                item_name,
                item_class,
                description
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(execution_id, item_type, item_key) do update set
                item_name = excluded.item_name,
                item_class = excluded.item_class,
                description = excluded.description
            """,
            (execution_id, item_type.value, clean_key, item_name, item_class, description),
        )
        row = conn.execute(
            """
            select id
            from execution_items
            where execution_id = ? and item_type = ? and item_key = ?
            """,
            (execution_id, item_type.value, clean_key),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"execution item 写入失败: {clean_key}")
        return int(row[0])

    def _clear_execution_items(
        self,
        conn: sqlite3.Connection,
        execution_id: int,
        item_type: ExecutionItemType | None = None,
    ) -> None:
        if item_type is None:
            conn.execute("delete from execution_items where execution_id = ?", (execution_id,))
            return
        item_type = ensure_execution_item_type(item_type)
        conn.execute(
            "delete from execution_items where execution_id = ? and item_type = ?",
            (execution_id, item_type.value),
        )

    def _replace_params(self, conn: sqlite3.Connection, execution_item_id: int, params: Mapping[str, Any]) -> None:
        conn.execute("delete from execution_item_params where execution_item_id = ?", (execution_item_id,))
        for key, value in sorted(params.items()):
            value_text, value_type = serialize_value(value)
            conn.execute(
                """
                insert into execution_item_params (
                    execution_item_id,
                    param_key,
                    param_value,
                    param_type
                ) values (?, ?, ?, ?)
                """,
                (execution_item_id, str(key), value_text, value_type),
            )

    def _replace_metrics(self, conn: sqlite3.Connection, execution_item_id: int, metrics: Mapping[str, Any]) -> None:
        conn.execute("delete from execution_item_metrics where execution_item_id = ?", (execution_item_id,))
        for key, value in sorted(metrics.items()):
            value_text, value_type = serialize_value(value)
            conn.execute(
                """
                insert into execution_item_metrics (
                    execution_item_id,
                    metric_key,
                    metric_value,
                    metric_type
                ) values (?, ?, ?, ?)
                """,
                (execution_item_id, str(key), value_text, value_type),
            )

    def _upsert_metrics(self, conn: sqlite3.Connection, execution_item_id: int, metrics: Mapping[str, Any]) -> None:
        for key, value in sorted(metrics.items()):
            value_text, value_type = serialize_value(value)
            conn.execute(
                """
                insert into execution_item_metrics (
                    execution_item_id,
                    metric_key,
                    metric_value,
                    metric_type
                ) values (?, ?, ?, ?)
                on conflict(execution_item_id, metric_key) do update set
                    metric_value = excluded.metric_value,
                    metric_type = excluded.metric_type
                """,
                (execution_item_id, str(key), value_text, value_type),
            )

    def _selection_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return self._records.selection_row_to_dict(conn, row)

    def _backtest_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return self._records.backtest_row_to_dict(conn, row)

    def _replace_backtest_selection_links(
        self,
        conn: sqlite3.Connection,
        backtest_execution_id: int,
        selection_execution_keys: list[str],
    ) -> None:
        conn.execute(
            "delete from backtest_selection_links where backtest_execution_id = ?",
            (backtest_execution_id,),
        )
        now = datetime.now().isoformat(timespec="seconds")
        for selection_key in selection_execution_keys:
            selection_id = self._get_execution_id(conn, selection_key)
            if selection_id is None:
                continue
            conn.execute(
                """
                insert or ignore into backtest_selection_links (
                    backtest_execution_id,
                    selection_execution_id,
                    created_at
                ) values (?, ?, ?)
                """,
                (backtest_execution_id, selection_id, now),
            )

    def _linked_selection_keys(self, conn: sqlite3.Connection, backtest_execution_id: int) -> list[str]:
        rows = conn.execute(
            """
            select sel.execution_key
            from backtest_selection_links link
            join executions sel on sel.id = link.selection_execution_id
            where link.backtest_execution_id = ?
            order by sel.created_at
            """,
            (backtest_execution_id,),
        ).fetchall()
        return [str(row["execution_key"]) for row in rows]

    def _upsert_execution_log_link(
        self,
        conn: sqlite3.Connection,
        *,
        job_execution_id: str,
        resource_type: str,
        resource_execution_key: str | None,
        resource_url: str | None,
        created_at: str,
    ) -> None:
        clean_job_id = str(job_execution_id or "").strip()
        clean_resource_type = str(resource_type or "").strip()
        if not clean_job_id or not clean_resource_type:
            return
        conn.execute(
            """
            insert into execution_log_links (
                job_execution_id,
                resource_type,
                resource_execution_key,
                resource_url,
                created_at
            ) values (?, ?, ?, ?, ?)
            on conflict(job_execution_id) do update set
                resource_type = excluded.resource_type,
                resource_execution_key = excluded.resource_execution_key,
                resource_url = excluded.resource_url
            """,
            (
                clean_job_id,
                clean_resource_type,
                str(resource_execution_key).strip() if resource_execution_key else None,
                str(resource_url).strip() if resource_url else None,
                created_at,
            ),
        )

    def _execution_items(
        self,
        conn: sqlite3.Connection,
        execution_id: int,
        item_type: ExecutionItemType,
    ) -> list[dict[str, Any]]:
        return self._records.execution_items(conn, execution_id, item_type)

    def _item_kv(
        self,
        conn: sqlite3.Connection,
        row_id: int,
        table: str,
        key_column: str,
        value_column: str,
        type_column: str,
    ) -> dict[str, Any]:
        return self._records.item_kv(conn, row_id, table, key_column, value_column, type_column)

    def _delete_runs(self, *, run_type: str, child_table: str, execution_keys: list[str] | None) -> dict[str, Any]:
        self.ensure_ready()
        normalized_execution_keys = normalize_execution_keys(execution_keys)
        run_type = str(run_type)
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if normalized_execution_keys is None:
                rows = conn.execute(
                    f"""
                    select er.id, er.execution_key, er.object_dir_key
                    from executions er
                    join {child_table} child on child.execution_id = er.id
                    """
                ).fetchall()
            elif not normalized_execution_keys:
                return delete_result(run_type, requested=0, deleted=0)
            else:
                placeholders = make_placeholders(len(normalized_execution_keys))
                rows = conn.execute(
                    f"""
                    select er.id, er.execution_key, er.object_dir_key
                    from executions er
                    join {child_table} child on child.execution_id = er.id
                    where er.execution_key in ({placeholders})
                    """,
                    tuple(normalized_execution_keys),
                ).fetchall()
            execution_ids = [int(row["id"]) for row in rows]
            deleted_execution_keys = [str(row["execution_key"]) for row in rows]
            artifact_keys: list[str] = []
            root_keys: list[str] = []
            scope_dir_keys: list[str] = []
            if execution_ids:
                placeholders = make_placeholders(len(execution_ids))
                if run_type == ExecutionType.BACKTEST.value:
                    conn.execute(
                        f"delete from backtest_selection_links where backtest_execution_id in ({placeholders})",
                        tuple(execution_ids),
                    )
                artifact_rows = conn.execute(
                    f"""
                    select storage_key
                    from artifacts
                    where artifact_scope = ? and execution_id in ({placeholders})
                    """,
                    (run_type, *execution_ids),
                ).fetchall()
                artifact_keys = [str(row["storage_key"] or "") for row in artifact_rows if row["storage_key"]]
                conn.execute(
                    f"""
                    delete from artifacts
                    where artifact_scope = ? and execution_id in ({placeholders})
                    """,
                    (run_type, *execution_ids),
                )
                conn.execute(
                    f"delete from {child_table} where execution_id in ({placeholders})",
                    tuple(execution_ids),
                )

                remaining_rows = conn.execute(
                    f"""
                    select
                        er.id,
                        er.object_dir_key,
                        exists(select 1 from selection_results sr where sr.execution_id = er.id) as has_selection,
                        exists(select 1 from backtest_results br where br.execution_id = er.id) as has_backtest
                    from executions er
                    where er.id in ({placeholders})
                    """,
                    tuple(execution_ids),
                ).fetchall()
                orphan_ids = [
                    int(row["id"])
                    for row in remaining_rows
                    if not int(row["has_selection"]) and not int(row["has_backtest"])
                ]
                for row in remaining_rows:
                    object_key = str(row["object_dir_key"] or "")
                    if int(row["id"]) in orphan_ids:
                        root_keys.append(object_key)
                    else:
                        scope_dir_keys.append(scoped_dir_key(object_key, run_type))
                if orphan_ids:
                    orphan_placeholders = make_placeholders(len(orphan_ids))
                    conn.execute(
                        f"delete from executions where id in ({orphan_placeholders})",
                        tuple(orphan_ids),
                    )

        storage_keys = dedupe_storage_keys([*root_keys, *scope_dir_keys, *artifact_keys])
        file_errors = [error for error in (self._delete_storage_key(key) for key in storage_keys) if error]
        requested = len(normalized_execution_keys) if normalized_execution_keys is not None else len(deleted_execution_keys)
        missing = [] if normalized_execution_keys is None else [
            execution_key for execution_key in normalized_execution_keys if execution_key not in set(deleted_execution_keys)
        ]
        return {
            "result_type": run_type,
            "requested": requested,
            "deleted": len(deleted_execution_keys),
            "missing": missing,
            "file_errors": file_errors,
        }

    def _delete_storage_key(self, storage_key: str) -> dict[str, str] | None:
        return self._artifacts.delete_storage_key(storage_key)

    def _execution_object_key(self, object_dir: Path) -> str:
        return self._connection.execution_object_key(object_dir)

    def _connect(self) -> sqlite3.Connection:
        return self._connection.connect()
