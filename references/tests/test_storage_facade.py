from __future__ import annotations

from core.storage import AppStorage, SelectionResultInUseError


def test_storage_facade_initializes_schema(tmp_path):
    storage = AppStorage(tmp_path / "storage")

    storage.ensure_ready()

    assert storage.db_path.exists()
    assert storage.objects_root.exists()


def test_storage_facade_exports_selection_in_use_error():
    assert issubclass(SelectionResultInUseError, ValueError)


def test_backtest_result_exposes_linked_selection_date_range(tmp_path):
    storage = AppStorage(tmp_path / "storage")
    strategy_snapshot = {
        "name": "测试策略",
        "class": "TestSelector",
        "description": "测试策略描述",
        "params": {},
    }

    for execution_key, selection_date in [
        ("selection_20260401", "2026-04-01"),
        ("selection_20260403", "2026-04-03"),
    ]:
        storage.record_selection_result(
            execution_key=execution_key,
            selection_date=selection_date,
            strategies=["测试策略"],
            strategy_snapshots=[strategy_snapshot],
            data_dir="db",
            signal_file=f"{execution_key}/signals.json",
            summaries=[
                {
                    "strategy": "测试策略",
                    "date": selection_date,
                    "count": 1,
                    "elapsed_seconds": 0.1,
                }
            ],
            object_dir=storage.objects_root / "executions" / execution_key / "selection",
        )

    storage.record_backtest_result(
        execution_key="backtest_20260401_20260403",
        meta={
            "created_at": "2026-04-04T10:00:00",
            "from": "2026-04-01",
            "to": "2026-04-03",
            "strategies": ["测试策略"],
            "strategy_snapshots": [strategy_snapshot],
            "signal_dir": "selection_history",
            "selection_execution_keys": ["selection_20260401", "selection_20260403"],
            "capital_mode": "unlimited_cash",
            "cash_per_trade": 50000,
            "trade_rule": {},
        },
        summaries=[{"strategy": "测试策略", "trade_count": 0}],
        object_dir=storage.objects_root / "executions" / "backtest_20260401_20260403" / "backtest",
    )

    result = storage.get_backtest_result("backtest_20260401_20260403")

    assert result is not None
    assert result["selection_from"] == "2026-04-01"
    assert result["selection_to"] == "2026-04-03"
