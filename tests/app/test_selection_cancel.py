"""Tests: a selection job must respond to cancellation between strategies.

Regression: with a single trading date and many strategies, the worker only
checked cancellation once per trading day, so cancelling a full-market
single-day job had no effect until every strategy finished (minutes).
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


class SlowSelector:
    """Strategy that simulates a long-running full-market computation."""

    def __init__(self, definition):
        self.definition = definition

    def select(self, context):
        time.sleep(3)
        from trendradar.domain.strategy.protocol import SelectionResult

        return SelectionResult(
            strategy_id=self.definition.strategy_id,
            strategy_name=self.definition.name,
            trade_date=context.trade_date,
            selected_codes=[],
        )


class TinyMarketStore:
    """One stock, one trading date: enough to enter the strategy loop."""

    def trading_dates(self, start, end):
        return [date(2026, 8, 20)]

    def load_bars(self, codes, start, end, columns=None):
        return pl.DataFrame(
            {
                "code": ["000001"],
                "date": [date(2026, 8, 20)],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "volume": [1000.0],
            }
        )

    def stock_meta(self, codes=None):
        return pl.DataFrame({"code": ["000001"]})


@pytest.fixture(autouse=True)
def _slow_strategies():
    """Register slow test strategies, restoring the registry afterwards."""
    import trendradar.domain.strategy.registry as registry
    from trendradar.domain.strategy.models import StrategyDefinition

    before = dict(registry._registry)
    for sid in ("slow_cancel_test", "slow_cancel_test_2"):
        registry.register(
            StrategyDefinition(
                strategy_id=sid,
                name=sid,
                description="",
                selector_class=SlowSelector,
                default_params={},
            )
        )
    yield
    registry._registry.clear()
    registry._registry.update(before)


def _wait_status(executor: JobExecutor, job_id: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = executor.get_state(job_id)["status"]
        # The worker writes "failed" (Cancelled by user) before the executor
        # flips it to "cancelled"; only the final state counts.
        if status == "cancelled":
            return status
        time.sleep(0.05)
    return executor.get_state(job_id)["status"]


def test_cancel_takes_effect_between_strategies(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())

    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.app.services.selection_service import submit_selection

    job_id = submit_selection(
        executor,
        TinyMarketStore(),
        {
            "start_date": "2026-08-20",
            "end_date": "2026-08-20",
            "strategies": ["slow_cancel_test", "slow_cancel_test_2"],
        },
        sc,
    )

    # Wait until the worker is inside the strategy loop (slow selector sleeping).
    deadline = time.time() + 10
    while time.time() < deadline:
        if executor.get_state(job_id)["status"] in ("success", "failed"):
            pytest.fail("job finished before the slow strategy started")
        logs = JobStore(sc.db_path).get_logs(job_id)
        if any("Processing 1 trading dates" in line for line in logs):
            break
        time.sleep(0.05)
    else:
        pytest.fail("worker never entered the strategy loop")

    assert executor.cancel(job_id) is True

    # Cancellation must take effect after the first slow strategy finishes and
    # before the second one runs (both sleep 3s; without the per-strategy check
    # the job would stay running until both complete, ~6s).
    t0 = time.time()
    status = _wait_status(executor, job_id, timeout=4.5)
    elapsed = time.time() - t0

    assert status == "cancelled"
    assert elapsed < 4.5, f"cancel took {elapsed:.2f}s, worker ignored it until all strategies finished"

    executor.shutdown(wait=True)
