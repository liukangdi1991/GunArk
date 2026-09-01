"""Service-level tests: successful jobs must register executions/artifacts metadata.

These cover the end-to-end metadata chain required by the V2 spec:
- selection registers an executions row + artifact rows
- backtest-from-selection registers a backtest execution + execution_links row (FK-safe)
- market sync registers an executions row (type=market_bars_sync, R12)
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


class EmptyMarketStore:
    """Boundary double: a market store with no data available."""

    def trading_dates(self, start, end):
        return []

    def load_bars(self, codes, start, end, columns=None):
        return pl.DataFrame(
            schema={
                "code": pl.Utf8,
                "date": pl.Date,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
                "amount": pl.Float64,
                "adj_factor": pl.Float64,
                "is_suspended": pl.Boolean,
            }
        )

    def stock_meta(self, codes=None):
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8})

    def get_calendar(self):
        return []


def _init_storage(tmp_path: Path) -> StorageConnection:
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return sc


def _run_job(executor: JobExecutor, job_id: str) -> None:
    executor._jobs[job_id].future.result(timeout=15.0)


def test_selection_job_registers_execution_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)
    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.app.services.selection_service import submit_selection

    job_id = submit_selection(
        executor,
        EmptyMarketStore(),
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
        sc,
    )
    _run_job(executor, job_id)

    assert executor.get_state(job_id)["status"] == "success"

    conn = sc.connect()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?", (job_id,)
    ).fetchone()
    assert row is not None
    assert row["execution_type"] == "selection"
    assert row["status"] == "success"
    assert row["manifest_key"] == f"objects/executions/{job_id}/selection/manifest.json"
    assert row["lineage_key"] == f"objects/executions/{job_id}/selection/lineage.json"

    artifacts = conn.execute(
        "SELECT artifact_type, storage_key FROM artifacts WHERE execution_key = ? "
        "ORDER BY artifact_type",
        (job_id,),
    ).fetchall()
    types = {a["artifact_type"] for a in artifacts}
    assert "selection/manifest.json" in types
    assert "selection/lineage.json" in types
    assert "selection/signals.json" in types
    for a in artifacts:
        assert a["storage_key"].startswith(f"objects/executions/{job_id}/selection/")
        assert a["storage_key"].endswith(a["artifact_type"].split("/", 1)[1])

    executor.shutdown(wait=True)


def test_backtest_job_links_to_existing_selection(tmp_path, monkeypatch):
    """Regression: backtest-from-selection must not fail with an FK violation."""
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)
    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.domain.signal.repository import SignalRepository
    from trendradar.infrastructure.storage.artifact_store import ArtifactStore

    repo = SignalRepository(ArtifactStore(sc.storage_root))

    from trendradar.app.services.selection_service import submit_selection

    selection_key = submit_selection(
        executor,
        EmptyMarketStore(),
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
        sc,
    )
    _run_job(executor, selection_key)
    assert executor.get_state(selection_key)["status"] == "success"

    from trendradar.app.services.backtest_service import submit_backtest

    job_id = submit_backtest(
        executor, EmptyMarketStore(), repo, {"execution_key": selection_key}
    )
    _run_job(executor, job_id)

    state = executor.get_state(job_id)
    assert state["status"] == "success", state.get("error")

    conn = sc.connect()
    bt = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?", (job_id,)
    ).fetchone()
    assert bt is not None
    assert bt["execution_type"] == "backtest"
    assert bt["status"] == "success"

    link = conn.execute(
        "SELECT * FROM execution_links WHERE source_execution_key = ? "
        "AND target_execution_key = ? AND link_type = 'backtest_uses_selection'",
        (selection_key, job_id),
    ).fetchone()
    assert link is not None

    artifacts = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE execution_key = ?", (job_id,)
    ).fetchall()
    types = {a["artifact_type"] for a in artifacts}
    assert "backtest/result.json" in types
    assert "backtest/metrics.json" in types

    executor.shutdown(wait=True)


def test_market_sync_execution_registration(tmp_path, monkeypatch):
    """R12：新引擎只登记 executions 行（type=market_bars_sync），
    不再有 market_sync_runs 表。"""
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = _init_storage(tmp_path)

    from trendradar.app.services.market_sync.service import (
        register_market_sync_execution,
    )

    register_market_sync_execution("20260827_180000_market_bars_sync_abcd")

    conn = sc.connect()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?",
        ("20260827_180000_market_bars_sync_abcd",),
    ).fetchone()
    assert row is not None
    assert row["execution_type"] == "market_bars_sync"

    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "market_sync_runs" not in tables


def test_selection_with_seeded_settings_and_default_group(tmp_path, monkeypatch):
    """Regression: selection must work when strategy_settings rows exist."""
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)

    from trendradar.app.services.strategy_service import ensure_default_group

    ensure_default_group(sc)

    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.app.services.selection_service import submit_selection

    job_id = submit_selection(
        executor,
        EmptyMarketStore(),
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "groups": ["default"]},
        sc,
    )
    _run_job(executor, job_id)

    state = executor.get_state(job_id)
    assert state["status"] == "success", state.get("error")

    conn = sc.connect()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?", (job_id,)
    ).fetchone()
    assert row is not None

    executor.shutdown(wait=True)


def test_selection_backtest_pipeline_registers_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)
    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.domain.signal.repository import SignalRepository
    from trendradar.infrastructure.storage.artifact_store import ArtifactStore

    repo = SignalRepository(ArtifactStore(sc.storage_root))

    from trendradar.app.services.backtest_service import submit_selection_backtest

    job_id = submit_selection_backtest(
        executor,
        EmptyMarketStore(),
        repo,
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
    )
    _run_job(executor, job_id)

    state = executor.get_state(job_id)
    assert state["status"] == "success", state.get("error")

    conn = sc.connect()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?", (job_id,)
    ).fetchone()
    assert row is not None
    assert row["execution_type"] == "selection_backtest"

    artifacts = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE execution_key = ?", (job_id,)
    ).fetchall()
    types = {a["artifact_type"] for a in artifacts}
    assert "selection/manifest.json" in types
    assert "selection/signals.json" in types
    assert "backtest/result.json" in types
    assert "backtest/metrics.json" in types

    # 组合管线共用 job_id：不应产生 source=target 的自引用血缘
    link = conn.execute(
        "SELECT * FROM execution_links WHERE source_execution_key = ? "
        "AND target_execution_key = ? AND link_type = 'backtest_uses_selection'",
        (job_id, job_id),
    ).fetchone()
    assert link is None

    executor.shutdown(wait=True)
