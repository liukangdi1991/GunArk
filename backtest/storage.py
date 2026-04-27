from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


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
                create table if not exists backtest_runs (
                    run_id text primary key,
                    created_at text not null,
                    finished_at text not null,
                    start_date text not null,
                    end_date text not null,
                    strategies_json text not null,
                    signal_dir text,
                    capital_mode text,
                    cash_per_trade real,
                    trade_rule_json text,
                    strategy_snapshots_json text not null default '[]',
                    status text not null,
                    summary_json text not null,
                    object_dir_key text not null
                );

                create table if not exists artifacts (
                    id integer primary key autoincrement,
                    run_type text not null default 'backtest',
                    run_id text not null,
                    artifact_type text not null,
                    storage_key text not null,
                    mime_type text,
                    size_bytes integer,
                    checksum text,
                    created_at text not null,
                    unique (run_type, run_id, artifact_type)
                );

                create table if not exists selection_runs (
                    run_id text primary key,
                    created_at text not null,
                    finished_at text not null,
                    selection_date text not null,
                    strategies_json text not null,
                    strategy_snapshots_json text not null default '[]',
                    data_dir text,
                    signal_file text,
                    status text not null,
                    summary_json text not null,
                    object_dir_key text not null
                );

                create index if not exists idx_backtest_runs_created_at
                    on backtest_runs(created_at);

                create index if not exists idx_artifacts_run_id
                    on artifacts(run_type, run_id);

                create index if not exists idx_selection_runs_created_at
                    on selection_runs(created_at);
                """
            )
            _ensure_column(conn, "backtest_runs", "strategy_snapshots_json", "text not null default '[]'")
            _ensure_generic_artifacts_schema(conn)
            conn.execute(
                "create index if not exists idx_artifacts_run_id on artifacts(run_type, run_id)"
            )

    def record_backtest_run(
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
        with self._connect() as conn:
            conn.execute(
                """
                insert into backtest_runs (
                    run_id,
                    created_at,
                    finished_at,
                    start_date,
                    end_date,
                    strategies_json,
                    signal_dir,
                    capital_mode,
                    cash_per_trade,
                    trade_rule_json,
                    strategy_snapshots_json,
                    status,
                    summary_json,
                    object_dir_key
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id) do update set
                    finished_at = excluded.finished_at,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    strategies_json = excluded.strategies_json,
                    signal_dir = excluded.signal_dir,
                    capital_mode = excluded.capital_mode,
                    cash_per_trade = excluded.cash_per_trade,
                    trade_rule_json = excluded.trade_rule_json,
                    strategy_snapshots_json = excluded.strategy_snapshots_json,
                    status = excluded.status,
                    summary_json = excluded.summary_json,
                    object_dir_key = excluded.object_dir_key
                """,
                (
                    run_id,
                    created_at,
                    now,
                    str(meta.get("from") or ""),
                    str(meta.get("to") or ""),
                    _json_dumps(meta.get("strategies", [])),
                    str(meta.get("signal_dir") or ""),
                    str(meta.get("capital_mode") or ""),
                    _as_optional_float(meta.get("cash_per_trade")),
                    _json_dumps(meta.get("trade_rule", {})),
                    _json_dumps(meta.get("strategy_snapshots", [])),
                    status,
                    _json_dumps(summaries),
                    self.storage_key(object_dir),
                ),
            )

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
            conn.execute(
                """
                insert into artifacts (
                    run_type,
                    run_id,
                    artifact_type,
                    storage_key,
                    mime_type,
                    size_bytes,
                    checksum,
                    created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_type, run_id, artifact_type) do update set
                    storage_key = excluded.storage_key,
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    checksum = excluded.checksum,
                    created_at = excluded.created_at
                """,
                (
                    run_type,
                    run_id,
                    artifact_type,
                    self.storage_key(path),
                    mime_type,
                    int(stat.st_size),
                    _sha256(path),
                    now,
                ),
            )

    def record_selection_run(
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
        with self._connect() as conn:
            conn.execute(
                """
                insert into selection_runs (
                    run_id,
                    created_at,
                    finished_at,
                    selection_date,
                    strategies_json,
                    strategy_snapshots_json,
                    data_dir,
                    signal_file,
                    status,
                    summary_json,
                    object_dir_key
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id) do update set
                    finished_at = excluded.finished_at,
                    selection_date = excluded.selection_date,
                    strategies_json = excluded.strategies_json,
                    strategy_snapshots_json = excluded.strategy_snapshots_json,
                    data_dir = excluded.data_dir,
                    signal_file = excluded.signal_file,
                    status = excluded.status,
                    summary_json = excluded.summary_json,
                    object_dir_key = excluded.object_dir_key
                """,
                (
                    run_id,
                    now,
                    now,
                    selection_date,
                    _json_dumps(strategies),
                    _json_dumps(strategy_snapshots),
                    data_dir,
                    signal_file,
                    status,
                    _json_dumps(summaries),
                    self.storage_key(object_dir),
                ),
            )

    def list_backtest_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    run_id,
                    created_at,
                    finished_at,
                    start_date,
                    end_date,
                    strategies_json,
                    capital_mode,
                    cash_per_trade,
                    strategy_snapshots_json,
                    status,
                    summary_json,
                    object_dir_key
                from backtest_runs
                order by created_at desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
        return [_decode_run_row(row) for row in rows]

    def get_backtest_run(self, run_id: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select
                    run_id,
                    created_at,
                    finished_at,
                    start_date,
                    end_date,
                    strategies_json,
                    signal_dir,
                    capital_mode,
                    cash_per_trade,
                    trade_rule_json,
                    strategy_snapshots_json,
                    status,
                    summary_json,
                    object_dir_key
                from backtest_runs
                where run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _decode_run_row(row)

    def list_selection_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select
                    run_id,
                    created_at,
                    finished_at,
                    selection_date,
                    strategies_json,
                    strategy_snapshots_json,
                    data_dir,
                    signal_file,
                    status,
                    summary_json,
                    object_dir_key
                from selection_runs
                order by created_at desc
                limit ?
                """,
                (int(limit),),
            ).fetchall()
        return [_decode_selection_row(row) for row in rows]

    def get_selection_run(self, run_id: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select
                    run_id,
                    created_at,
                    finished_at,
                    selection_date,
                    strategies_json,
                    strategy_snapshots_json,
                    data_dir,
                    signal_file,
                    status,
                    summary_json,
                    object_dir_key
                from selection_runs
                where run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _decode_selection_row(row)

    def list_artifacts(self, run_id: str, run_type: str | None = None) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            where = "where run_id = ?"
            params: tuple[Any, ...] = (run_id,)
            if run_type is not None:
                where = "where run_type = ? and run_id = ?"
                params = (run_type, run_id)
            rows = conn.execute(
                f"""
                select
                    run_type,
                    artifact_type,
                    storage_key,
                    mime_type,
                    size_bytes,
                    checksum,
                    created_at
                from artifacts
                {where}
                order by artifact_type
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def artifact_path(self, storage_key: str) -> Path:
        return self.objects_root / storage_key

    def storage_key(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.objects_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("pragma foreign_keys = on")
        return conn


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _decode_run_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["strategies"] = _json_loads(data.pop("strategies_json", None), [])
    data["summary"] = _json_loads(data.pop("summary_json", None), [])
    data["strategy_snapshots"] = _json_loads(data.pop("strategy_snapshots_json", None), [])
    if "trade_rule_json" in data:
        data["trade_rule"] = _json_loads(data.pop("trade_rule_json", None), {})
    return data


def _decode_selection_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["strategies"] = _json_loads(data.pop("strategies_json", None), [])
    data["summary"] = _json_loads(data.pop("summary_json", None), [])
    data["strategy_snapshots"] = _json_loads(data.pop("strategy_snapshots_json", None), [])
    return data


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")


def _ensure_generic_artifacts_schema(conn: sqlite3.Connection) -> None:
    fk_rows = conn.execute("pragma foreign_key_list(artifacts)").fetchall()
    columns = {row[1] for row in conn.execute("pragma table_info(artifacts)").fetchall()}
    if not fk_rows and "run_type" in columns:
        return

    conn.execute("alter table artifacts rename to artifacts_old")
    conn.execute(
        """
        create table artifacts (
            id integer primary key autoincrement,
            run_type text not null default 'backtest',
            run_id text not null,
            artifact_type text not null,
            storage_key text not null,
            mime_type text,
            size_bytes integer,
            checksum text,
            created_at text not null,
            unique (run_type, run_id, artifact_type)
        )
        """
    )
    old_columns = {row[1] for row in conn.execute("pragma table_info(artifacts_old)").fetchall()}
    if old_columns:
        run_type_expr = "run_type" if "run_type" in old_columns else "'backtest'"
        conn.execute(
            f"""
            insert or ignore into artifacts (
                id,
                run_type,
                run_id,
                artifact_type,
                storage_key,
                mime_type,
                size_bytes,
                checksum,
                created_at
            )
            select
                id,
                {run_type_expr},
                run_id,
                artifact_type,
                storage_key,
                mime_type,
                size_bytes,
                checksum,
                created_at
            from artifacts_old
            """
        )
    conn.execute("drop table artifacts_old")


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
