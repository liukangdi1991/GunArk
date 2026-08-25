"""Backtest report payload completeness.

The frontend types (BacktestTrade/BacktestEquity/BacktestSkip) require
execution_key (+ strategy on equity), and the report UI renders
sell_postpone_days as the "延期" column. The worker must serialize all of
them; metrics must carry initial_cash/final_cash (the presenter reads them
and currently falls back to 0).
"""

import json
from datetime import date, timedelta

from trendradar.domain.backtest.config import BacktestConfig, ExecutionConfig
from trendradar.domain.signal.models import SignalSet, StrategySignal
from trendradar.app.services.backtest_service import _run_backtest_worker


def _make_market(days):
    bars = {}
    for d in days:
        bars[d] = {
            "code": "000001", "date": d,
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.4,
            "volume": 1_000_000.0,
        }

    class FakeMarket:
        def get_calendar(self):
            return list(days)

        def get_row(self, code, dt):
            return bars.get(dt)

        def get_previous_close(self, code, dt):
            idx = days.index(dt)
            if idx == 0:
                return None
            return bars[days[idx - 1]]["close"]

    return FakeMarket()


def test_worker_report_serializes_contract_fields():
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]

    class FakeCtx:
        def __init__(self):
            self.job_id = "bt-report-test"
            self.cancelled = False
            self.result = None

        def log(self, message, level="INFO"):
            pass

        def update_progress(self, current, total, message=""):
            pass

        def check_cancelled(self):
            return self.cancelled

        def fail(self, error):
            pass

        def succeed(self, result):
            self.result = result

    ctx = FakeCtx()
    signal_set = SignalSet(
        execution_key="sel-1",
        signals=[StrategySignal(
            strategy_id="s1", strategy_name="S1", signal_date=days[0], codes=["000001"],
        )],
    )
    config = BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1))

    _run_backtest_worker(ctx, signal_set, _make_market(days), config)

    out = ctx.result
    assert out is not None
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["execution_key"] == "bt-report-test"
    assert trade["sell_postpone_days"] == 0
    assert out["equity_curve"], "equity curve must be non-empty"
    eq = out["equity_curve"][0]
    assert eq["execution_key"] == "bt-report-test"
    assert "strategy" in eq
    assert out["metrics"]["initial_cash"] == config.capital.initial_cash
    assert out["metrics"]["final_cash"] > 0


def test_backtest_config_reads_nested_capital(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    conn = sc.connect()
    conn.execute(
        "INSERT INTO jobs (job_id, job_type, status, request_json) VALUES (?, ?, ?, ?)",
        (
            "job_selbt", "selection_backtest", "success",
            json.dumps({
                "start_date": "2026-08-01", "end_date": "2026-08-30",
                "backtest": {
                    "capital": {"mode": "realistic", "fixed_cash_per_trade": 80000},
                    "execution": {},
                },
                "trade_strategy": None,
            }),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (job_id, job_type, status, request_json) VALUES (?, ?, ?, ?)",
        (
            "job_btfs", "backtest", "success",
            json.dumps({
                "execution_key": "sel-1",
                "capital": {"mode": "realistic", "fixed_cash_per_trade": 60000},
                "execution": {},
                "trade_strategy": None,
            }),
        ),
    )
    conn.commit()
    conn.close()

    from trendradar.interfaces.api.presenters import _backtest_config

    nested = _backtest_config("job_selbt")
    assert nested["capital_mode"] == "realistic"
    assert nested["cash_per_trade"] == 80000

    top = _backtest_config("job_btfs")
    assert top["capital_mode"] == "realistic"
    assert top["cash_per_trade"] == 60000
