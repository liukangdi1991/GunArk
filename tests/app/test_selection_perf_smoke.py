"""Loose smoke guard: selection over a small market must finish quickly."""

import time
from datetime import date, timedelta

import polars as pl


def _make_tiny_market(n_codes: int = 50, n_days: int = 120):
    class TinyMarket:
        def trading_dates(self, start, end):
            return [date(2026, 8, 20)]

        def load_bars(self, codes, start, end, columns=None):
            rows = []
            for i, c in enumerate(codes[:n_codes]):
                for d in range(n_days):
                    rows.append({
                        "code": c,
                        "date": date(2026, 8, 20) - timedelta(days=n_days - d),
                        "open": 10.0, "high": 10.5, "low": 9.5,
                        "close": 10.0 + d * 0.001, "volume": 1000000.0,
                    })
            return pl.DataFrame(rows)

        def stock_meta(self, codes=None):
            return pl.DataFrame({"code": [f"{i:06d}" for i in range(n_codes)]})

    return TinyMarket()


def test_small_selection_completes_quickly(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all
    from trendradar.app.jobs.executor import JobExecutor
    from trendradar.app.jobs.persistence import JobStore
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    from trendradar.app.services.selection_service import submit_selection

    register_all()
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    t0 = time.time()
    job = submit_selection(
        ex, _make_tiny_market(),
        {"start_date": "2026-08-20", "end_date": "2026-08-20"}, sc,
    )
    ex._jobs[job].future.result(timeout=30)
    assert ex.get_state(job)["status"] == "success"
    assert time.time() - t0 < 30
    ex.shutdown(wait=True)
