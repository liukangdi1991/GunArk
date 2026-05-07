from __future__ import annotations

from datetime import date

import polars as pl


class _FakeRunner:
    def run_selection(self, *, date_obj, data_table, get_data_dict):  # noqa: ANN001
        return ["000001"] if date_obj == date(2026, 4, 1) else ["000002"]


def test_batch_selection_persists_one_range_execution(tmp_path, monkeypatch) -> None:
    from core.storage import AppStorage
    from web.schemas.selection import BatchSelectionRequest
    from web.services import selection_service

    app_storage = AppStorage(tmp_path / "storage")
    monkeypatch.setattr(selection_service, "storage", app_storage)
    monkeypatch.setattr(selection_service, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(
        selection_service,
        "load_data_table",
        lambda data_dir, tickers: pl.DataFrame({"code": ["000001"], "date": [date(2026, 4, 1)]}),
    )
    monkeypatch.setattr(
        selection_service,
        "load_strategies_from_config",
        lambda config_path: {"测试策略": {"selector": object()}},
    )
    monkeypatch.setattr(selection_service, "build_strategy_runner", lambda selector: _FakeRunner())
    monkeypatch.setattr(selection_service, "_load_stock_meta", lambda: {})
    monkeypatch.setattr(
        selection_service,
        "_strategy_snapshots",
        lambda names: [{"name": name, "class": "FakeSelector", "description": "", "params": {}} for name in names],
    )
    monkeypatch.setattr(
        selection_service,
        "_collect_batch_trade_dates",
        lambda payload: (
            [date(2026, 4, 1), date(2026, 4, 2)],
            "2026-04-01 ~ 2026-04-02",
            {"from": "2026-04-01", "to": "2026-04-02"},
        ),
    )

    result = selection_service.create_batch_selection(
        BatchSelectionRequest(**{"from": "2026-04-01", "to": "2026-04-02", "strategies": ["测试策略"]})
    )

    execution_key = str(result["execution_key"])
    rows = app_storage.list_selection_results()
    signal_files = sorted((app_storage.objects_root / "executions" / execution_key / "selection" / "signals").glob("*.json"))

    assert len(rows) == 1
    assert rows[0]["execution_key"] == execution_key
    assert rows[0]["selection_from"] == "2026-04-01"
    assert rows[0]["selection_to"] == "2026-04-02"
    assert rows[0]["trade_days"] == 2
    assert [path.name for path in signal_files] == ["20260401.json", "20260402.json"]


def test_backtest_resolves_batch_selection_manifest_signal_files(tmp_path, monkeypatch) -> None:
    from core.storage import AppStorage
    from web.schemas.selection import BatchSelectionRequest
    from web.services import backtest_service, selection_service

    app_storage = AppStorage(tmp_path / "storage")
    monkeypatch.setattr(selection_service, "storage", app_storage)
    monkeypatch.setattr(backtest_service, "storage", app_storage)
    monkeypatch.setattr(selection_service, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(
        selection_service,
        "load_data_table",
        lambda data_dir, tickers: pl.DataFrame({"code": ["000001"], "date": [date(2026, 4, 1)]}),
    )
    monkeypatch.setattr(
        selection_service,
        "load_strategies_from_config",
        lambda config_path: {"测试策略": {"selector": object()}},
    )
    monkeypatch.setattr(selection_service, "build_strategy_runner", lambda selector: _FakeRunner())
    monkeypatch.setattr(selection_service, "_load_stock_meta", lambda: {})
    monkeypatch.setattr(
        selection_service,
        "_strategy_snapshots",
        lambda names: [{"name": name, "class": "FakeSelector", "description": "", "params": {}} for name in names],
    )
    monkeypatch.setattr(
        selection_service,
        "_collect_batch_trade_dates",
        lambda payload: (
            [date(2026, 4, 1), date(2026, 4, 2)],
            "2026-04-01 ~ 2026-04-02",
            {"from": "2026-04-01", "to": "2026-04-02"},
        ),
    )
    result = selection_service.create_batch_selection(
        BatchSelectionRequest(**{"from": "2026-04-01", "to": "2026-04-02", "strategies": ["测试策略"]})
    )

    signal_files, start, end, source_keys = backtest_service._resolve_selection_signal_files([result["execution_key"]])

    assert [path.name for path in signal_files] == ["20260401.json", "20260402.json"]
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 2)
    assert source_keys == [result["execution_key"]]


def test_selection_history_uses_persisted_range_instead_of_legacy_job_group(tmp_path, monkeypatch) -> None:
    import json

    from core.storage import AppStorage
    from web.services import selection_service

    app_storage = AppStorage(tmp_path / "storage")
    monkeypatch.setattr(selection_service, "storage", app_storage)

    object_dir = app_storage.objects_root / "executions" / "selection_20260402" / "selection"
    object_dir.mkdir(parents=True)
    signal_file = object_dir / "signals.json"
    signal_file.write_text("{}", encoding="utf-8")
    app_storage.record_selection_result(
        execution_key="selection_20260402",
        selection_date="2026-04-02",
        strategies=["测试策略"],
        strategy_snapshots=[{"name": "测试策略", "class": "FakeSelector", "description": "", "params": {}}],
        data_dir=str(tmp_path / "db"),
        signal_file=str(signal_file),
        summaries=[{"strategy": "测试策略", "date": "2026-04-02", "count": 1, "elapsed_seconds": 0.1}],
        object_dir=object_dir,
    )

    job_dir = app_storage.objects_root / "jobs" / "legacy_batch_job"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "execution_id": "legacy_batch_job",
                "execution_type": "selection_batch",
                "result": {
                    "trade_from": "2026-03-02",
                    "trade_to": "2026-04-02",
                    "results": [
                        {"execution_key": "selection_20260402", "selection_date": "2026-04-02"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    row = selection_service.list_selections(limit=1)["results"][0]

    assert row["selection_from"] == "2026-04-02"
    assert row["selection_to"] == "2026-04-02"
    assert row["trade_days"] == 1
