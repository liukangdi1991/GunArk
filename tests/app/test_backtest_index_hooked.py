"""回测 worker 落盘后建索引（P3）；作业终态只存摘要（P4）。

明细只落盘：`jobs.result_json` 曾把整个输出（3.6 万笔交易 ≈ 12.9MB）写进 SQLite，
而全仓无消费方——这里把「不落库」钉成契约。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from trendradar.app.services.backtest_service import _run_backtest_worker
from trendradar.domain.backtest.config import BacktestConfig, ExecutionConfig
from trendradar.domain.signal.models import SignalSet, StrategySignal


class _FakeCtx:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.messages: list[str] = []
        self.result = None

    def log(self, message: str, level: str = "INFO") -> None:
        self.messages.append(message)

    def update_progress(self, current: int, total: int, message: str = "") -> None:
        pass

    def check_cancelled(self) -> bool:
        return False

    def fail(self, error: str) -> None:
        raise AssertionError(error)

    def succeed(self, result) -> None:
        self.result = result


def _flat_market(days):
    bars = {
        d: {"code": "000001", "date": d, "open": 10.0, "high": 10.5, "low": 9.8,
            "close": 10.4, "volume": 1_000_000.0}
        for d in days
    }

    class FakeMarket:
        def get_calendar(self):
            return list(days)

        def get_row(self, code, dt):
            return bars.get(dt)

        def get_rows(self, code, start_dt, end_dt):
            return [bars[d] for d in days if start_dt <= d <= end_dt]

        def get_previous_close(self, code, dt):
            idx = days.index(dt)
            return None if idx == 0 else bars[days[idx - 1]]["close"]

    return FakeMarket()


def test_backtest_worker_writes_index_and_slim_job_result(tmp_path, monkeypatch):
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())

    key = "bt-index-test"
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    signal_set = SignalSet(
        execution_key=key,
        signals=[
            StrategySignal(
                strategy_id="s1", strategy_name="S1", signal_date=days[0], codes=["000001"]
            )
        ],
    )
    out_dir = tmp_path / "storage" / "objects" / "executions" / key / "backtest"
    ctx = _FakeCtx(key)

    _run_backtest_worker(
        ctx,
        signal_set,
        _flat_market(days),
        BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
        str(out_dir / "result.json"),
    )

    # P3：落盘后索引已就绪，且与产物同源（签名 = result.json 的 mtime）
    summary = json.loads((out_dir / ".index" / "summary.json").read_text(encoding="utf-8"))
    result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert summary["trade_count"] == result["trade_count"]
    assert summary["source_signature"] == {
        "result.json": (out_dir / "result.json").stat().st_mtime_ns
    }

    # P4：作业终态只存摘要——明细（trades/skips/equity）不得进库
    assert ctx.result is not None
    assert not {"trades", "skips", "open_positions", "equity_curve"} & set(ctx.result)
    assert set(ctx.result) >= {"metrics", "trade_count", "skip_count", "open_position_count"}
    assert len(json.dumps(ctx.result, ensure_ascii=False)) < 4096
