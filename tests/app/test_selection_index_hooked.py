"""选股 worker 落盘后必须已生成索引（新产物免自愈）。"""
from __future__ import annotations

import json

import polars as pl

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


class EmptyMarketStore:
    """空宇宙 store：本测试只验索引接线，不关心中选什么（与 test_execution_registration 同款）。"""

    def load_bars(self, codes, start, end, columns=None):
        return pl.DataFrame(schema={"code": pl.Utf8, "date": pl.Date, "close": pl.Float64})

    def latest_trade_date(self):
        return None

    def trading_dates(self, start, end):
        return []

    def stock_meta(self, codes=None):
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8})

    def get_calendar(self):
        return []

    def get_row(self, code, dt):
        return None

    def get_previous_close(self, code, dt):
        return None

    def get_rows(self, code, start_date, end_date):
        return pl.DataFrame(schema={"code": pl.Utf8, "date": pl.Date, "close": pl.Float64})


def _run_selection_job(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.app.services.selection_service import submit_selection

    job_id = submit_selection(
        executor,
        EmptyMarketStore(),
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
        sc,
    )
    executor._jobs[job_id].future.result(timeout=15.0)
    return executor, job_id


def test_selection_worker_writes_index(tmp_path, monkeypatch):
    executor, job_id = _run_selection_job(tmp_path, monkeypatch)
    try:
        assert executor.get_state(job_id)["status"] == "success"
        sel_dir = tmp_path / "storage" / "objects" / "executions" / job_id / "selection"
        assert (sel_dir / ".index" / "picks.parquet").is_file(), "worker 落盘后必须已建索引（免首访自愈）"
        summary = json.loads((sel_dir / ".index" / "summary.json").read_text(encoding="utf-8"))
        assert summary["total_picks"] == pl.read_parquet(sel_dir / ".index" / "picks.parquet").height
        assert summary["source_signature"] == {
            "signals.json": (sel_dir / "signals.json").stat().st_mtime_ns
        }
    finally:
        executor.shutdown(wait=True)
