"""SQLite storage layer for selection/backtest executions and artifacts."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from core.storage_migrations import (
    backfill_backtest_selection_links,
    backfill_execution_log_links,
    ensure_artifacts_scope_schema,
)
from core.storage_models import ExecutionItemType, ExecutionType, SelectionResultInUseError
from core.storage_schema import SCHEMA_SQL
from core.storage_utils import (
    backtest_metrics,
    coerce_metric,
    dedupe_storage_keys,
    delete_result,
    deserialize_value,
    ensure_execution_item_type,
    ensure_execution_type,
    execution_type_from_legacy,
    item_to_strategy_snapshot,
    normalize_execution_keys,
    placeholders as make_placeholders,
    scoped_dir_key,
    selection_metrics,
    serialize_value,
    sha256,
    snapshots_in_order,
)


class AppStorage:
    def __init__(self, storage_root: Path | str = "storage") -> None:
        self.storage_root = Path(storage_root)
        self.db_path = self.storage_root / "app.db"
        self.objects_root = self.storage_root / "objects"
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
        self.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        object_dir_key = self._execution_object_key(object_dir)
        with self._connect() as conn:
            execution_id = self._upsert_execution(
                conn,
                execution_key=execution_key,
                execution_type=ExecutionType.SELECTION,
                status=status,
                created_at=now,
                finished_at=now,
                object_dir_key=object_dir_key,
            )
            conn.execute(
                """
                insert into selection_results (
                    execution_id,
                    selection_date,
                    data_dir,
                    signal_file
                ) values (?, ?, ?, ?)
                on conflict(execution_id) do update set
                    selection_date = excluded.selection_date,
                    data_dir = excluded.data_dir,
                    signal_file = excluded.signal_file
                """,
                (execution_id, selection_date, data_dir, signal_file),
            )
            self._clear_execution_items(conn, execution_id, ExecutionItemType.SELECTION_STRATEGY)
            summary_by_strategy = {
                str(item.get("strategy") or ""): item
                for item in summaries
                if isinstance(item, dict)
            }
            for snapshot in snapshots_in_order(strategies, strategy_snapshots):
                item_id = self._upsert_execution_item(
                    conn,
                    execution_id=execution_id,
                    item_type=ExecutionItemType.SELECTION_STRATEGY,
                    item_key=str(snapshot.get("name") or ""),
                    item_name=str(snapshot.get("name") or ""),
                    item_class=str(snapshot.get("class") or ""),
                    description=str(snapshot.get("description") or ""),
                )
                self._replace_params(conn, item_id, snapshot.get("params") or {})
                metrics = selection_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
                self._upsert_metrics(conn, item_id, metrics)

    def record_backtest_result(
        self,
        *,
        execution_key: str,
        meta: Mapping[str, Any],
        summaries: list[dict[str, Any]],
        object_dir: Path,
        status: str = "success",
    ) -> None:
        self.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        created_at = str(meta.get("created_at") or now)
        object_dir_key = self._execution_object_key(object_dir)
        with self._connect() as conn:
            execution_id = self._upsert_execution(
                conn,
                execution_key=execution_key,
                execution_type=ExecutionType.BACKTEST,
                status=status,
                created_at=created_at,
                finished_at=now,
                object_dir_key=object_dir_key,
            )
            conn.execute(
                """
                insert into backtest_results (
                    execution_id,
                    start_date,
                    end_date,
                    signal_dir
                ) values (?, ?, ?, ?)
                on conflict(execution_id) do update set
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    signal_dir = excluded.signal_dir
                """,
                (
                    execution_id,
                    str(meta.get("from") or ""),
                    str(meta.get("to") or ""),
                    str(meta.get("signal_dir") or ""),
                ),
            )
            selection_keys = normalize_execution_keys([
                str(item) for item in (meta.get("selection_execution_keys") or [])
            ]) or []
            self._replace_backtest_selection_links(conn, execution_id, selection_keys)
            self._clear_execution_items(conn, execution_id, ExecutionItemType.CAPITAL_MODEL)
            self._clear_execution_items(conn, execution_id, ExecutionItemType.TRADE_STRATEGY)
            summary_by_strategy = {
                str(item.get("strategy") or ""): item
                for item in summaries
                if isinstance(item, dict)
            }
            strategy_names = [str(item) for item in (meta.get("strategies") or [])]
            for snapshot in snapshots_in_order(strategy_names, meta.get("strategy_snapshots") or []):
                item_id = self._upsert_execution_item(
                    conn,
                    execution_id=execution_id,
                    item_type=ExecutionItemType.SELECTION_STRATEGY,
                    item_key=str(snapshot.get("name") or ""),
                    item_name=str(snapshot.get("name") or ""),
                    item_class=str(snapshot.get("class") or ""),
                    description=str(snapshot.get("description") or ""),
                )
                self._replace_params(conn, item_id, snapshot.get("params") or {})
                metrics = backtest_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
                self._upsert_metrics(conn, item_id, metrics)

            capital_item_id = self._upsert_execution_item(
                conn,
                execution_id=execution_id,
                item_type=ExecutionItemType.CAPITAL_MODEL,
                item_key=str(meta.get("capital_mode") or ""),
                item_name=str(meta.get("capital_mode") or ""),
                item_class="",
                description="",
            )
            self._replace_params(
                conn,
                capital_item_id,
                {
                    "cash_per_trade": meta.get("cash_per_trade"),
                },
            )

            trade_rule = dict(meta.get("trade_rule") or {})
            trade_item_id = self._upsert_execution_item(
                conn,
                execution_id=execution_id,
                item_type=ExecutionItemType.TRADE_STRATEGY,
                item_key="fixed_hold_n_days",
                item_name="FixedHoldNDaysTradeRule",
                item_class="FixedHoldNDaysTradeRule",
                description="固定持仓 N 个交易日，含强制卖出规则。",
            )
            self._replace_params(conn, trade_item_id, trade_rule)

    def register_artifact(
        self,
        *,
        execution_key: str,
        artifact_type: str,
        path: Path,
        mime_type: str,
        run_type: str = "backtest",
    ) -> None:
        self.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        stat = path.stat()
        with self._connect() as conn:
            execution_id = self._get_execution_id(conn, execution_key)
            if execution_id is None:
                execution_id = self._upsert_execution(
                    conn,
                    execution_key=execution_key,
                    execution_type=execution_type_from_legacy(run_type),
                    status="success",
                    created_at=now,
                    finished_at=now,
                    object_dir_key=self._execution_object_key(path.parent),
                )
            conn.execute(
                """
                insert into artifacts (
                    execution_id,
                    artifact_scope,
                    artifact_type,
                    storage_key,
                    mime_type,
                    size_bytes,
                    checksum,
                    created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(execution_id, artifact_scope, artifact_type) do update set
                    storage_key = excluded.storage_key,
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    checksum = excluded.checksum,
                    created_at = excluded.created_at
                """,
                (
                    execution_id,
                    str(run_type),
                    artifact_type,
                    self.storage_key(path),
                    mime_type,
                    int(stat.st_size),
                    sha256(path),
                    now,
                ),
            )

    def record_execution_log_link(
        self,
        *,
        job_execution_id: str,
        resource_type: str,
        resource_execution_key: str | None,
        resource_url: str | None,
    ) -> None:
        self.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            self._upsert_execution_log_link(
                conn,
                job_execution_id=job_execution_id,
                resource_type=resource_type,
                resource_execution_key=resource_execution_key,
                resource_url=resource_url,
                created_at=now,
            )

    def list_selection_results(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    er.id as execution_id,
                    er.execution_key as execution_key,
                    er.created_at,
                    er.finished_at,
                    er.status,
                    er.object_dir_key,
                    sr.selection_date,
                    sr.data_dir,
                    sr.signal_file
                from executions er
                join selection_results sr on sr.execution_id = er.id
                order by er.created_at desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
            return [self._selection_row_to_dict(conn, row) for row in rows]

    def get_selection_result(self, execution_key: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select
                    er.id as execution_id,
                    er.execution_key as execution_key,
                    er.created_at,
                    er.finished_at,
                    er.status,
                    er.object_dir_key,
                    sr.selection_date,
                    sr.data_dir,
                    sr.signal_file
                from executions er
                join selection_results sr on sr.execution_id = er.id
                where er.execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            return None if row is None else self._selection_row_to_dict(conn, row)

    def list_backtest_results(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    er.id as execution_id,
                    er.execution_key as execution_key,
                    er.created_at,
                    er.finished_at,
                    er.status,
                    er.object_dir_key,
                    br.start_date,
                    br.end_date,
                    br.signal_dir
                from executions er
                join backtest_results br on br.execution_id = er.id
                order by er.created_at desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
            return [self._backtest_row_to_dict(conn, row) for row in rows]

    def get_backtest_result(self, execution_key: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select
                    er.id as execution_id,
                    er.execution_key as execution_key,
                    er.created_at,
                    er.finished_at,
                    er.status,
                    er.object_dir_key,
                    br.start_date,
                    br.end_date,
                    br.signal_dir
                from executions er
                join backtest_results br on br.execution_id = er.id
                where er.execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            return None if row is None else self._backtest_row_to_dict(conn, row)

    def list_artifacts(self, execution_key: str, run_type: str | None = None) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    a.artifact_scope,
                    a.artifact_type,
                    a.storage_key,
                    a.mime_type,
                    a.size_bytes,
                    a.checksum,
                    a.created_at
                from artifacts a
                join executions er on er.id = a.execution_id
                where er.execution_key = ?
                    and (? is null or a.artifact_scope = ?)
                order by a.artifact_type
                """,
                (execution_key, run_type, run_type),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_backtest_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self._delete_runs(
            run_type=ExecutionType.BACKTEST.value,
            child_table="backtest_results",
            execution_keys=execution_keys,
        )

    def delete_selection_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        blockers = self.selection_delete_blockers(execution_keys)
        if blockers:
            raise SelectionResultInUseError(blockers)
        return self._delete_runs(
            run_type=ExecutionType.SELECTION.value,
            child_table="selection_results",
            execution_keys=execution_keys,
        )

    def selection_delete_blockers(self, execution_keys: list[str] | None = None) -> list[dict[str, Any]]:
        self.ensure_ready()
        normalized_execution_keys = normalize_execution_keys(execution_keys)
        if normalized_execution_keys == []:
            return []
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            params: tuple[Any, ...] = ()
            where_sql = ""
            if normalized_execution_keys is not None:
                placeholder_sql = make_placeholders(len(normalized_execution_keys))
                where_sql = f"where sel.execution_key in ({placeholder_sql})"
                params = tuple(normalized_execution_keys)
            rows = conn.execute(
                f"""
                select
                    sel.execution_key as selection_execution_key,
                    bt.execution_key as backtest_execution_key
                from backtest_selection_links link
                join executions sel on sel.id = link.selection_execution_id
                join executions bt on bt.id = link.backtest_execution_id
                join selection_results sr on sr.execution_id = sel.id
                join backtest_results br on br.execution_id = bt.id
                {where_sql}
                order by sel.created_at desc, bt.created_at desc
                """,
                params,
            ).fetchall()

        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(str(row["selection_execution_key"]), []).append(str(row["backtest_execution_key"]))
        return [
            {
                "selection_execution_key": selection_key,
                "backtest_execution_keys": backtest_keys,
            }
            for selection_key, backtest_keys in grouped.items()
        ]

    def artifact_path(self, storage_key: str) -> Path:
        return self.objects_root / storage_key

    def storage_key(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.objects_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

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
        execution_items = self._execution_items(conn, int(row["execution_id"]), ExecutionItemType.SELECTION_STRATEGY)
        strategies = [item["item_name"] for item in execution_items]
        snapshots = [item_to_strategy_snapshot(item) for item in execution_items]
        summary = []
        for item in execution_items:
            metrics = item["metrics"]
            summary.append(
                {
                    "strategy": item["item_name"],
                    "date": row["selection_date"],
                    "count": coerce_metric(metrics.get("selected_count"), 0),
                    "elapsed_seconds": coerce_metric(metrics.get("elapsed_seconds"), 0.0),
                }
            )
        return {
            "execution_key": row["execution_key"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "selection_date": row["selection_date"],
            "strategies": strategies,
            "strategy_snapshots": snapshots,
            "data_dir": row["data_dir"],
            "signal_file": row["signal_file"],
            "status": row["status"],
            "summary": summary,
            "object_dir_key": row["object_dir_key"],
        }

    def _backtest_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        strategy_items = self._execution_items(conn, int(row["execution_id"]), ExecutionItemType.SELECTION_STRATEGY)
        capital_items = self._execution_items(conn, int(row["execution_id"]), ExecutionItemType.CAPITAL_MODEL)
        trade_items = self._execution_items(conn, int(row["execution_id"]), ExecutionItemType.TRADE_STRATEGY)
        strategies = [item["item_name"] for item in strategy_items]
        snapshots = [item_to_strategy_snapshot(item) for item in strategy_items]
        summary = []
        for item in strategy_items:
            metrics = item["metrics"]
            row_data = {"strategy": item["item_name"]}
            for key, value in metrics.items():
                row_data[key] = value
            summary.append(row_data)
        capital = capital_items[0] if capital_items else None
        trade_rule = trade_items[0] if trade_items else None
        return {
            "execution_key": row["execution_key"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "strategies": strategies,
            "capital_mode": capital["item_key"] if capital else None,
            "cash_per_trade": (capital["params"].get("cash_per_trade") if capital else None),
            "strategy_snapshots": snapshots,
            "status": row["status"],
            "summary": summary,
            "object_dir_key": row["object_dir_key"],
            "signal_dir": row["signal_dir"],
            "selection_execution_keys": self._linked_selection_keys(conn, int(row["execution_id"])),
            "trade_rule": trade_rule["params"] if trade_rule else {},
        }

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
        item_type = ensure_execution_item_type(item_type)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select
                id,
                item_type,
                item_key,
                item_name,
                item_class,
                description
            from execution_items
            where execution_id = ? and item_type = ?
            order by id
            """,
            (execution_id, item_type.value),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "item_type": row["item_type"],
                "item_key": row["item_key"],
                "item_name": row["item_name"],
                "item_class": row["item_class"],
                "description": row["description"],
                "params": self._item_kv(conn, int(row["id"]), "execution_item_params", "param_key", "param_value", "param_type"),
                "metrics": self._item_kv(conn, int(row["id"]), "execution_item_metrics", "metric_key", "metric_value", "metric_type"),
            }
            for row in rows
        ]

    def _item_kv(
        self,
        conn: sqlite3.Connection,
        row_id: int,
        table: str,
        key_column: str,
        value_column: str,
        type_column: str,
    ) -> dict[str, Any]:
        rows = conn.execute(
            f"""
            select {key_column} as key, {value_column} as value, {type_column} as value_type
            from {table}
            where execution_item_id = ?
            order by {key_column}
            """,
            (row_id,),
        ).fetchall()
        return {str(row["key"]): deserialize_value(row["value"], row["value_type"]) for row in rows}

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
        if not storage_key:
            return None
        try:
            root = self.objects_root.resolve()
            path = (self.objects_root / storage_key).resolve()
            path.relative_to(root)
        except ValueError:
            return {"storage_key": storage_key, "error": "storage key 超出 objects 目录，已拒绝删除"}

        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            return {"storage_key": storage_key, "error": str(exc)}
        return None

    def _execution_object_key(self, object_dir: Path) -> str:
        key = self.storage_key(object_dir)
        parts = key.split("/")
        if len(parts) >= 2 and parts[0] == "executions":
            return "/".join(parts[:2])
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("pragma foreign_keys = on")
        return conn
