"""Service-level tests: submit_market_sync wires force/codes through to sync_market.

Covers the Task 9 contract: the job worker refreshes stock_meta first, then
calls sync_market with the full request (force/codes passthrough) and
registers executions + market_sync_runs metadata on success.
"""

from __future__ import annotations

import polars as pl

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def test_submit_market_sync_passes_force_and_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    captured = {}

    from datetime import date

    from trendradar.infrastructure.tushare.syncer import SyncPlan

    def fake_sync_stock_list(bars_dir):
        return pl.DataFrame({"code": ["000001"], "name": ["平安银行"]})

    def fake_plan_sync(pro, bars_dir, cache_dir, request, now_utc=None):
        return SyncPlan(
            mode="incremental", missing_days=0, missing_dates=[],
            start=date(2026, 8, 1), end=date(2026, 8, 20), latest=date(2026, 8, 20),
            all_trade=set(), done=set(), uptodate=False, force=False, retry_codes=[],
        )

    def fake_sync_market(pro, bars_dir, cache_dir, request, now_utc=None, progress=None,
                         cancel_check=None, plan=None):
        captured.update(request)
        return {"mode": "incremental", "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": True}

    from trendradar.app.services import market_service
    # Patch the symbols referenced inside submit_market_sync's worker:
    # - market_service.syncer_module.sync_market / plan_sync (module attribute)
    # - stocklist.sync_stock_list (worker does a call-time import)
    # - market_service.get_pro (module-level import) to avoid the network
    monkeypatch.setattr(market_service.syncer_module, "plan_sync", fake_plan_sync)
    monkeypatch.setattr(market_service.syncer_module, "sync_market", fake_sync_market)
    monkeypatch.setattr(
        "trendradar.infrastructure.tushare.stocklist.sync_stock_list",
        fake_sync_stock_list,
    )
    monkeypatch.setattr(market_service, "get_pro", lambda: object())

    job_id = market_service.submit_market_sync(
        ex,
        {"codes": ["000001"], "force": True,
         "start_date": "2026-08-01", "end_date": "2026-08-20"},
        bars_dir=tmp_path / "storage" / "market" / "bars",
    )
    try:
        ex._jobs[job_id].future.result(timeout=10)
        assert ex.get_state(job_id)["status"] == "success"
        assert captured["force"] is True
        assert captured["codes"] == ["000001"]
        assert captured["start_date"] == "2026-08-01"
        assert captured["end_date"] == "2026-08-20"
    finally:
        ex.shutdown(wait=True)


def test_submit_market_sync_short_circuits_when_uptodate(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    from datetime import date

    import polars as pl

    from trendradar.infrastructure.tushare.syncer import SyncPlan

    def fake_sync_stock_list(bars_dir):
        return pl.DataFrame({"code": ["000001"], "name": ["平安银行"]})

    def fake_plan_sync(pro, bars_dir, cache_dir, request, now_utc=None):
        return SyncPlan(
            mode="incremental", missing_days=0, missing_dates=[],
            start=date(2026, 8, 1), end=date(2026, 8, 20), latest=date(2026, 8, 20),
            all_trade=set(), done=set(), uptodate=True, force=False, retry_codes=[],
        )

    calls = {"sync_market": 0}

    def fake_sync_market(pro, bars_dir, cache_dir, request, now_utc=None, progress=None,
                         cancel_check=None, plan=None):
        calls["sync_market"] += 1
        return {}

    from trendradar.app.services import market_service
    monkeypatch.setattr(market_service.syncer_module, "plan_sync", fake_plan_sync)
    monkeypatch.setattr(market_service.syncer_module, "sync_market", fake_sync_market)
    monkeypatch.setattr(
        "trendradar.infrastructure.tushare.stocklist.sync_stock_list",
        fake_sync_stock_list,
    )
    monkeypatch.setattr(market_service, "get_pro", lambda: object())

    job_id = market_service.submit_market_sync(
        ex,
        {"start_date": "2026-08-01", "end_date": "2026-08-20"},
        bars_dir=tmp_path / "storage" / "market" / "bars",
    )
    try:
        ex._jobs[job_id].future.result(timeout=10)
        assert ex.get_state(job_id)["status"] == "success"
        assert calls["sync_market"] == 0  # 无 codes 且 up-to-date → 短路
    finally:
        ex.shutdown(wait=True)


def test_submit_market_sync_with_codes_bypasses_uptodate(tmp_path, monkeypatch):
    """指定 codes 时即使 up-to-date 也必须执行同步。"""
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    ex = JobExecutor(JobStore(sc.db_path))

    from datetime import date

    import polars as pl

    from trendradar.infrastructure.tushare.syncer import SyncPlan

    def fake_sync_stock_list(bars_dir):
        return pl.DataFrame({"code": ["920099"], "name": ["瑞华技术"]})

    def fake_plan_sync(pro, bars_dir, cache_dir, request, now_utc=None):
        return SyncPlan(
            mode="incremental", missing_days=0, missing_dates=[],
            start=date(2026, 8, 1), end=date(2026, 8, 20), latest=date(2026, 8, 20),
            all_trade=set(), done=set(), uptodate=True, force=False, retry_codes=[],
        )

    calls = {"sync_market": 0, "request_codes": None}

    def fake_sync_market(pro, bars_dir, cache_dir, request, now_utc=None, progress=None,
                         cancel_check=None, plan=None):
        calls["sync_market"] += 1
        calls["request_codes"] = request.get("codes")
        return {"mode": "incremental", "missing_days": 0, "synced_days": 0,
                "synced_codes": 0, "new_codes": 0, "failed_days": 0,
                "failed_codes": 0, "retry_rounds": 0, "skipped_uptodate": False}

    from trendradar.app.services import market_service
    monkeypatch.setattr(market_service.syncer_module, "plan_sync", fake_plan_sync)
    monkeypatch.setattr(market_service.syncer_module, "sync_market", fake_sync_market)
    monkeypatch.setattr(
        "trendradar.infrastructure.tushare.stocklist.sync_stock_list",
        fake_sync_stock_list,
    )
    monkeypatch.setattr(market_service, "get_pro", lambda: object())

    job_id = market_service.submit_market_sync(
        ex,
        {"codes": ["920099"], "start_date": "2026-08-01", "end_date": "2026-08-20"},
        bars_dir=tmp_path / "storage" / "market" / "bars",
    )
    try:
        ex._jobs[job_id].future.result(timeout=10)
        assert ex.get_state(job_id)["status"] == "success"
        assert calls["sync_market"] == 1  # 未短路
        assert calls["request_codes"] == ["920099"]
    finally:
        ex.shutdown(wait=True)
