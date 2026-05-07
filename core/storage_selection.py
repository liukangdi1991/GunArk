from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from core.storage_models import ExecutionItemType, ExecutionType, SelectionResultInUseError
from core.storage_utils import (
    normalize_execution_keys,
    placeholders as make_placeholders,
    selection_metrics,
    snapshots_in_order,
)


class SelectionRepository:
    """Persistence operations for selection executions."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def record_selection_result(
        self,
        *,
        execution_key: str,
        selection_date: str,
        selection_from: str | None = None,
        selection_to: str | None = None,
        trade_days: int = 1,
        strategies: list[str],
        strategy_snapshots: list[dict[str, Any]],
        data_dir: str,
        signal_file: str,
        summaries: list[dict[str, Any]],
        object_dir: Path,
        status: str = "success",
    ) -> None:
        self.storage.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        object_dir_key = self.storage._execution_object_key(object_dir)
        with self.storage._connect() as conn:
            execution_id = self.storage._upsert_execution(
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
                    selection_from,
                    selection_to,
                    trade_days,
                    data_dir,
                    signal_file
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(execution_id) do update set
                    selection_date = excluded.selection_date,
                    selection_from = excluded.selection_from,
                    selection_to = excluded.selection_to,
                    trade_days = excluded.trade_days,
                    data_dir = excluded.data_dir,
                    signal_file = excluded.signal_file
                """,
                (
                    execution_id,
                    selection_date,
                    selection_from or selection_date,
                    selection_to or selection_date,
                    max(1, int(trade_days or 1)),
                    data_dir,
                    signal_file,
                ),
            )
            self.storage._clear_execution_items(conn, execution_id, ExecutionItemType.SELECTION_STRATEGY)
            summary_by_strategy = {
                str(item.get("strategy") or ""): item
                for item in summaries
                if isinstance(item, dict)
            }
            for snapshot in snapshots_in_order(strategies, strategy_snapshots):
                item_id = self.storage._upsert_execution_item(
                    conn,
                    execution_id=execution_id,
                    item_type=ExecutionItemType.SELECTION_STRATEGY,
                    item_key=str(snapshot.get("name") or ""),
                    item_name=str(snapshot.get("name") or ""),
                    item_class=str(snapshot.get("class") or ""),
                    description=str(snapshot.get("description") or ""),
                )
                self.storage._replace_params(conn, item_id, snapshot.get("params") or {})
                metrics = selection_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
                self.storage._upsert_metrics(conn, item_id, metrics)

    def list_selection_results(self, limit: int = 50) -> list[dict[str, Any]]:
        self.storage.ensure_ready()
        with self.storage._connect() as conn:
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
                    sr.selection_from,
                    sr.selection_to,
                    sr.trade_days,
                    sr.data_dir,
                    sr.signal_file
                from executions er
                join selection_results sr on sr.execution_id = er.id
                order by er.created_at desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
            return [self.storage._selection_row_to_dict(conn, row) for row in rows]

    def get_selection_result(self, execution_key: str) -> dict[str, Any] | None:
        self.storage.ensure_ready()
        with self.storage._connect() as conn:
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
                    sr.selection_from,
                    sr.selection_to,
                    sr.trade_days,
                    sr.data_dir,
                    sr.signal_file
                from executions er
                join selection_results sr on sr.execution_id = er.id
                where er.execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            return None if row is None else self.storage._selection_row_to_dict(conn, row)

    def delete_selection_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        blockers = self.selection_delete_blockers(execution_keys)
        if blockers:
            raise SelectionResultInUseError(blockers)
        return self.storage._delete_runs(
            run_type=ExecutionType.SELECTION.value,
            child_table="selection_results",
            execution_keys=execution_keys,
        )

    def selection_delete_blockers(self, execution_keys: list[str] | None = None) -> list[dict[str, Any]]:
        self.storage.ensure_ready()
        normalized_execution_keys = normalize_execution_keys(execution_keys)
        if normalized_execution_keys == []:
            return []
        with self.storage._connect() as conn:
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
