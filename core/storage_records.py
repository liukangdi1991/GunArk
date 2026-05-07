from __future__ import annotations

import sqlite3
from typing import Any

from core.storage_models import ExecutionItemType
from core.storage_utils import coerce_metric, deserialize_value, ensure_execution_item_type, item_to_strategy_snapshot


class StorageRecordMapper:
    """Maps normalized SQLite rows into API-facing dictionaries."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def selection_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        execution_items = self.execution_items(conn, int(row["execution_id"]), ExecutionItemType.SELECTION_STRATEGY)
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
            "selection_from": row["selection_from"] or row["selection_date"],
            "selection_to": row["selection_to"] or row["selection_date"],
            "trade_days": int(row["trade_days"] or 1),
            "strategies": strategies,
            "strategy_snapshots": snapshots,
            "data_dir": row["data_dir"],
            "signal_file": row["signal_file"],
            "status": row["status"],
            "summary": summary,
            "object_dir_key": row["object_dir_key"],
        }

    def backtest_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        execution_id = int(row["execution_id"])
        strategy_items = self.execution_items(conn, int(row["execution_id"]), ExecutionItemType.SELECTION_STRATEGY)
        capital_items = self.execution_items(conn, int(row["execution_id"]), ExecutionItemType.CAPITAL_MODEL)
        trade_items = self.execution_items(conn, int(row["execution_id"]), ExecutionItemType.TRADE_STRATEGY)
        selection_dates = self.linked_selection_dates(conn, execution_id)
        selection_from = selection_dates[0] if selection_dates else None
        selection_to = selection_dates[-1] if selection_dates else None
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
            "selection_execution_keys": self.storage._linked_selection_keys(conn, execution_id),
            "selection_from": selection_from,
            "selection_to": selection_to,
            "trade_rule": trade_rule["params"] if trade_rule else {},
        }

    def linked_selection_dates(self, conn: sqlite3.Connection, backtest_execution_id: int) -> list[str]:
        rows = conn.execute(
            """
            select sr.selection_date
            from backtest_selection_links link
            join selection_results sr on sr.execution_id = link.selection_execution_id
            where link.backtest_execution_id = ?
            order by sr.selection_date
            """,
            (backtest_execution_id,),
        ).fetchall()
        return [str(row["selection_date"]) for row in rows if row["selection_date"]]

    def execution_items(
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
                "params": self.item_kv(
                    conn,
                    int(row["id"]),
                    "execution_item_params",
                    "param_key",
                    "param_value",
                    "param_type",
                ),
                "metrics": self.item_kv(
                    conn,
                    int(row["id"]),
                    "execution_item_metrics",
                    "metric_key",
                    "metric_value",
                    "metric_type",
                ),
            }
            for row in rows
        ]

    def item_kv(
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
