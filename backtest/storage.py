"""SQLite storage layer for selection/backtest executions and artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class ExecutionType(StrEnum):
    SELECTION = "selection"
    BACKTEST = "backtest"
    SELECTION_BACKTEST = "selection_backtest"


class ExecutionItemType(StrEnum):
    SELECTION_STRATEGY = "selection_strategy"
    TRADE_STRATEGY = "trade_strategy"
    CAPITAL_MODEL = "capital_model"
    EXECUTION_CONFIG = "execution_config"


class BacktestStorage:
    def __init__(self, storage_root: Path | str = "storage") -> None:
        self.storage_root = Path(storage_root)
        self.db_path = self.storage_root / "app.db"
        self.objects_root = self.storage_root / "objects"

    def ensure_ready(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists executions (
                    id integer primary key autoincrement,
                    execution_key text not null unique,
                    execution_type text not null,
                    status text not null,
                    created_at text not null,
                    finished_at text not null,
                    object_dir_key text not null
                );

                create table if not exists selection_results (
                    id integer primary key autoincrement,
                    execution_id integer not null unique,
                    selection_date text not null,
                    data_dir text,
                    signal_file text,
                    foreign key (execution_id) references executions(id) on delete cascade
                );

                create table if not exists backtest_results (
                    id integer primary key autoincrement,
                    execution_id integer not null unique,
                    start_date text not null,
                    end_date text not null,
                    signal_dir text,
                    foreign key (execution_id) references executions(id) on delete cascade
                );

                create table if not exists execution_items (
                    id integer primary key autoincrement,
                    execution_id integer not null,
                    item_type text not null,
                    item_key text not null,
                    item_name text not null,
                    item_class text,
                    description text,
                    foreign key (execution_id) references executions(id) on delete cascade,
                    unique (execution_id, item_type, item_key)
                );

                create table if not exists execution_item_params (
                    id integer primary key autoincrement,
                    execution_item_id integer not null,
                    param_key text not null,
                    param_value text,
                    param_type text not null,
                    foreign key (execution_item_id) references execution_items(id) on delete cascade,
                    unique (execution_item_id, param_key)
                );

                create table if not exists execution_item_metrics (
                    id integer primary key autoincrement,
                    execution_item_id integer not null,
                    metric_key text not null,
                    metric_value text,
                    metric_type text not null,
                    foreign key (execution_item_id) references execution_items(id) on delete cascade,
                    unique (execution_item_id, metric_key)
                );

                create table if not exists artifacts (
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
                );

                create index if not exists idx_executions_created_at
                    on executions(created_at);
                create index if not exists idx_execution_items_execution_id
                    on execution_items(execution_id);
                create index if not exists idx_artifacts_execution_id
                    on artifacts(execution_id);
                """
            )
            self._ensure_artifacts_scope_schema(conn)

    def record_selection_result(
        self,
        *,
        run_id: str,
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
                run_id=run_id,
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
            for snapshot in _snapshots_in_order(strategies, strategy_snapshots):
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
                metrics = _selection_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
                self._upsert_metrics(conn, item_id, metrics)

    def record_backtest_result(
        self,
        *,
        run_id: str,
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
                run_id=run_id,
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
            self._clear_execution_items(conn, execution_id, ExecutionItemType.CAPITAL_MODEL)
            self._clear_execution_items(conn, execution_id, ExecutionItemType.TRADE_STRATEGY)
            summary_by_strategy = {
                str(item.get("strategy") or ""): item
                for item in summaries
                if isinstance(item, dict)
            }
            strategy_names = [str(item) for item in (meta.get("strategies") or [])]
            for snapshot in _snapshots_in_order(strategy_names, meta.get("strategy_snapshots") or []):
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
                metrics = _backtest_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
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
        run_id: str,
        artifact_type: str,
        path: Path,
        mime_type: str,
        run_type: str = "backtest",
    ) -> None:
        self.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        stat = path.stat()
        with self._connect() as conn:
            execution_id = self._get_execution_id(conn, run_id)
            if execution_id is None:
                execution_id = self._upsert_execution(
                    conn,
                    run_id=run_id,
                    execution_type=_execution_type_from_legacy(run_type),
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
                    _sha256(path),
                    now,
                ),
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

    def get_selection_result(self, run_id: str) -> dict[str, Any] | None:
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
                (run_id,),
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

    def get_backtest_result(self, run_id: str) -> dict[str, Any] | None:
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
                (run_id,),
            ).fetchone()
            return None if row is None else self._backtest_row_to_dict(conn, row)

    def list_artifacts(self, run_id: str, run_type: str | None = None) -> list[dict[str, Any]]:
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
                (run_id, run_type, run_type),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_backtest_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self._delete_runs(
            run_type=ExecutionType.BACKTEST.value,
            child_table="backtest_results",
            execution_keys=execution_keys,
        )

    def delete_selection_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self._delete_runs(
            run_type=ExecutionType.SELECTION.value,
            child_table="selection_results",
            execution_keys=execution_keys,
        )

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
        run_id: str,
        execution_type: ExecutionType,
        status: str,
        created_at: str,
        finished_at: str,
        object_dir_key: str,
    ) -> int:
        execution_type = _ensure_execution_type(execution_type)
        existing = conn.execute(
            "select id, execution_type, created_at from executions where execution_key = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            existing_type = _ensure_execution_type(str(existing[1]))
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
            (run_id, execution_type.value, status, created_at, finished_at, object_dir_key),
        )
        existing_id = self._get_execution_id(conn, run_id)
        if existing_id is None:
            raise RuntimeError(f"execution 写入失败: {run_id}")
        return existing_id

    def _get_execution_id(self, conn: sqlite3.Connection, run_id: str) -> int | None:
        row = conn.execute("select id from executions where execution_key = ?", (run_id,)).fetchone()
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
        item_type = _ensure_execution_item_type(item_type)
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
        item_type = _ensure_execution_item_type(item_type)
        conn.execute(
            "delete from execution_items where execution_id = ? and item_type = ?",
            (execution_id, item_type.value),
        )

    def _replace_params(self, conn: sqlite3.Connection, execution_item_id: int, params: Mapping[str, Any]) -> None:
        conn.execute("delete from execution_item_params where execution_item_id = ?", (execution_item_id,))
        for key, value in sorted(params.items()):
            value_text, value_type = _serialize_value(value)
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
            value_text, value_type = _serialize_value(value)
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
            value_text, value_type = _serialize_value(value)
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
        snapshots = [_item_to_strategy_snapshot(item) for item in execution_items]
        summary = []
        for item in execution_items:
            metrics = item["metrics"]
            summary.append(
                {
                    "strategy": item["item_name"],
                    "date": row["selection_date"],
                    "count": _coerce_metric(metrics.get("selected_count"), 0),
                    "elapsed_seconds": _coerce_metric(metrics.get("elapsed_seconds"), 0.0),
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
        snapshots = [_item_to_strategy_snapshot(item) for item in strategy_items]
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
            "trade_rule": trade_rule["params"] if trade_rule else {},
        }

    def _execution_items(
        self,
        conn: sqlite3.Connection,
        execution_id: int,
        item_type: ExecutionItemType,
    ) -> list[dict[str, Any]]:
        item_type = _ensure_execution_item_type(item_type)
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
        return {str(row["key"]): _deserialize_value(row["value"], row["value_type"]) for row in rows}

    def _delete_runs(self, *, run_type: str, child_table: str, execution_keys: list[str] | None) -> dict[str, Any]:
        self.ensure_ready()
        normalized_execution_keys = _normalize_execution_keys(execution_keys)
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
                return _delete_result(run_type, requested=0, deleted=0)
            else:
                placeholders = _placeholders(len(normalized_execution_keys))
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
                placeholders = _placeholders(len(execution_ids))
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
                        scope_dir_keys.append(_scoped_dir_key(object_key, run_type))
                if orphan_ids:
                    orphan_placeholders = _placeholders(len(orphan_ids))
                    conn.execute(
                        f"delete from executions where id in ({orphan_placeholders})",
                        tuple(orphan_ids),
                    )

        storage_keys = _dedupe_storage_keys([*root_keys, *scope_dir_keys, *artifact_keys])
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

    def _ensure_artifacts_scope_schema(self, conn: sqlite3.Connection) -> None:
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


def _ensure_execution_type(value: ExecutionType | str) -> ExecutionType:
    try:
        return value if isinstance(value, ExecutionType) else ExecutionType(str(value))
    except ValueError as exc:
        raise ValueError(f"非法 execution type: {value}") from exc


def _ensure_execution_item_type(value: ExecutionItemType | str) -> ExecutionItemType:
    try:
        return value if isinstance(value, ExecutionItemType) else ExecutionItemType(str(value))
    except ValueError as exc:
        raise ValueError(f"非法 execution item type: {value}") from exc


def _execution_type_from_legacy(value: str) -> ExecutionType:
    return ExecutionType.SELECTION if str(value) == "selection" else ExecutionType.BACKTEST


def _snapshots_in_order(strategy_names: list[str], strategy_snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {
        str(item.get("name") or ""): item
        for item in strategy_snapshots
        if isinstance(item, dict)
    }
    result = []
    for name in strategy_names:
        result.append(by_name.get(name, {"name": name, "class": "", "description": "", "params": {}}))
    return result


def _selection_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selected_count": summary.get("count", 0),
        "elapsed_seconds": summary.get("elapsed_seconds", 0.0),
    }


def _backtest_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "strategy"}


def _item_to_strategy_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("item_name") or item.get("item_key") or "",
        "class": item.get("item_class") or "",
        "description": item.get("description") or "",
        "params": dict(item.get("params") or {}),
    }


def _serialize_value(value: Any) -> tuple[str | None, str]:
    value = _native_scalar(value)
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return "true" if value else "false", "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value), "integer"
    if isinstance(value, float):
        return repr(float(value)), "number"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), "json"
    return str(value), "string"


def _deserialize_value(value: Any, value_type: Any) -> Any:
    if value_type == "null":
        return None
    if value is None:
        return None
    text = str(value)
    if value_type == "boolean":
        return text.lower() == "true"
    if value_type == "integer":
        try:
            return int(text)
        except ValueError:
            return text
    if value_type == "number":
        try:
            return float(text)
        except ValueError:
            parsed = _parse_legacy_numpy_scalar(text)
            return parsed if parsed is not None else text
    if value_type == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    parsed = _parse_legacy_numpy_scalar(text)
    return parsed if parsed is not None else text


def _native_scalar(value: Any) -> Any:
    if _is_numpy_scalar(value):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _is_numpy_scalar(value: Any) -> bool:
    module = type(value).__module__
    return module == "numpy" or module.startswith("numpy.")


def _parse_legacy_numpy_scalar(text: str) -> float | int | bool | None:
    normalized = text.strip()
    if normalized in {"np.True_", "np.bool_(True)"}:
        return True
    if normalized in {"np.False_", "np.bool_(False)"}:
        return False

    match = re.fullmatch(r"np\.(?:float\d*|int\d*)\(([-+0-9.eE]+)\)", normalized)
    if not match:
        return None
    raw = match.group(1)
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if normalized.startswith("np.int") and value.is_integer() else value


def _coerce_metric(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value


def _delete_result(run_type: str, *, requested: int, deleted: int) -> dict[str, Any]:
    return {
        "result_type": run_type,
        "requested": requested,
        "deleted": deleted,
        "missing": [],
        "file_errors": [],
    }


def _normalize_execution_keys(execution_keys: list[str] | None) -> list[str] | None:
    if execution_keys is None:
        return None
    normalized = []
    seen = set()
    for value in execution_keys:
        execution_key = str(value or "").strip()
        if not execution_key or execution_key in seen:
            continue
        normalized.append(execution_key)
        seen.add(execution_key)
    return normalized


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _dedupe_storage_keys(storage_keys: list[str]) -> list[str]:
    normalized = [key for key in dict.fromkeys(storage_keys) if key]
    directories = [key.rstrip("/") for key in normalized]
    result = []
    for key in normalized:
        clean_key = key.rstrip("/")
        if any(clean_key != directory and clean_key.startswith(f"{directory}/") for directory in directories):
            continue
        result.append(key)
    return result


def _scoped_dir_key(object_dir_key: str, run_type: str) -> str:
    clean_key = str(object_dir_key or "").rstrip("/")
    if not clean_key:
        return ""
    if run_type not in {ExecutionType.SELECTION.value, ExecutionType.BACKTEST.value}:
        return clean_key
    return f"{clean_key}/{run_type}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
