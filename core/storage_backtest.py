from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from core.storage_models import ExecutionItemType, ExecutionType
from core.storage_utils import backtest_metrics, normalize_execution_keys, snapshots_in_order


class BacktestRepository:
    """Persistence operations for backtest executions."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def record_backtest_result(
        self,
        *,
        execution_key: str,
        meta: Mapping[str, Any],
        summaries: list[dict[str, Any]],
        object_dir: Path,
        status: str = "success",
    ) -> None:
        self.storage.ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        created_at = str(meta.get("created_at") or now)
        object_dir_key = self.storage._execution_object_key(object_dir)
        with self.storage._connect() as conn:
            execution_id = self.storage._upsert_execution(
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
            self.storage._replace_backtest_selection_links(conn, execution_id, selection_keys)
            self.storage._clear_execution_items(conn, execution_id, ExecutionItemType.CAPITAL_MODEL)
            self.storage._clear_execution_items(conn, execution_id, ExecutionItemType.TRADE_STRATEGY)
            summary_by_strategy = {
                str(item.get("strategy") or ""): item
                for item in summaries
                if isinstance(item, dict)
            }
            strategy_names = [str(item) for item in (meta.get("strategies") or [])]
            for snapshot in snapshots_in_order(strategy_names, meta.get("strategy_snapshots") or []):
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
                metrics = backtest_metrics(summary_by_strategy.get(str(snapshot.get("name") or ""), {}))
                self.storage._upsert_metrics(conn, item_id, metrics)

            capital_item_id = self.storage._upsert_execution_item(
                conn,
                execution_id=execution_id,
                item_type=ExecutionItemType.CAPITAL_MODEL,
                item_key=str(meta.get("capital_mode") or ""),
                item_name=str(meta.get("capital_mode") or ""),
                item_class="",
                description="",
            )
            self.storage._replace_params(
                conn,
                capital_item_id,
                {
                    "cash_per_trade": meta.get("cash_per_trade"),
                },
            )

            trade_rule = dict(meta.get("trade_rule") or {})
            trade_item_id = self.storage._upsert_execution_item(
                conn,
                execution_id=execution_id,
                item_type=ExecutionItemType.TRADE_STRATEGY,
                item_key=str(trade_rule.get("trade_strategy") or "long_term_bull_bear_stop"),
                item_name=str(trade_rule.get("trade_strategy_name") or "不限资金 + 多空线止损"),
                item_class="FixedHoldNDaysTradeRule",
                description=str(trade_rule.get("trade_strategy_name") or "固定持仓 N 个交易日，含强制卖出规则。"),
            )
            self.storage._replace_params(conn, trade_item_id, trade_rule)

    def list_backtest_results(self, limit: int = 50) -> list[dict[str, Any]]:
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
            return [self.storage._backtest_row_to_dict(conn, row) for row in rows]

    def get_backtest_result(self, execution_key: str) -> dict[str, Any] | None:
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
                    br.start_date,
                    br.end_date,
                    br.signal_dir
                from executions er
                join backtest_results br on br.execution_id = er.id
                where er.execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            return None if row is None else self.storage._backtest_row_to_dict(conn, row)

    def delete_backtest_results(self, execution_keys: list[str] | None = None) -> dict[str, Any]:
        return self.storage._delete_runs(
            run_type=ExecutionType.BACKTEST.value,
            child_table="backtest_results",
            execution_keys=execution_keys,
        )
