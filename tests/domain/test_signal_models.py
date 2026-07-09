from __future__ import annotations
from datetime import date, timedelta
import json
import tempfile
from pathlib import Path

import pytest

from trendradar.domain.signal.models import StrategySignal, SignalSet
from trendradar.domain.signal.repository import SignalRepository
from trendradar.infrastructure.storage.artifact_store import ArtifactStore


def test_strategy_signal_creation():
    s = StrategySignal(
        strategy_id="bbi_kdj_b1",
        strategy_name="B1战法",
        group_ids=["default"],
        primary_group_id="default",
        signal_date=date(2026, 7, 9),
        codes=["000001", "600519"],
    )
    assert s.strategy_id == "bbi_kdj_b1"
    assert s.strategy_name == "B1战法"
    assert s.signal_date == date(2026, 7, 9)
    assert len(s.codes) == 2


def test_strategy_signal_defaults():
    s = StrategySignal(strategy_id="test", strategy_name="Test")
    assert s.group_ids == []
    assert s.primary_group_id == ""
    assert s.signal_date is None
    assert s.codes == []


def test_signal_set_roundtrip():
    ss = SignalSet(
        execution_key="20260709_120000_selection",
        signal_from=date(2026, 7, 1),
        signal_to=date(2026, 7, 9),
        strategies_snapshot=[{"id": "bbi_kdj_b1", "name": "B1战法"}],
        signals=[
            StrategySignal(
                strategy_id="bbi_kdj_b1",
                strategy_name="B1战法",
                group_ids=["default"],
                primary_group_id="default",
                signal_date=date(2026, 7, 9),
                codes=["000001", "600519"],
            ),
        ],
    )
    json_str = ss.to_json()
    parsed = SignalSet.from_json(json_str)
    assert parsed.execution_key == "20260709_120000_selection"
    assert len(parsed.signals) == 1
    assert parsed.signals[0].codes == ["000001", "600519"]
    assert parsed.signals[0].strategy_id == "bbi_kdj_b1"


def test_empty_signal_set():
    ss = SignalSet(execution_key="test")
    json_str = ss.to_json()
    parsed = SignalSet.from_json(json_str)
    assert parsed.signals == []
    assert parsed.schema_version == "2.0"


def test_multi_signal_set():
    ss = SignalSet(
        execution_key="multi_test",
        signal_from=date(2026, 7, 1),
        signal_to=date(2026, 7, 9),
        signals=[
            StrategySignal(
                strategy_id="bbi_kdj_b1",
                strategy_name="B1战法",
                group_ids=["default"],
                primary_group_id="default",
                signal_date=date(2026, 7, 9),
                codes=["000001"],
            ),
            StrategySignal(
                strategy_id="peak_kdj",
                strategy_name="峰KDJ",
                group_ids=["default"],
                primary_group_id="default",
                signal_date=date(2026, 7, 8),
                codes=["600519", "000858"],
            ),
        ],
    )
    json_str = ss.to_json()
    parsed = SignalSet.from_json(json_str)
    assert len(parsed.signals) == 2
    assert parsed.signals[0].strategy_id == "bbi_kdj_b1"
    assert parsed.signals[1].strategy_id == "peak_kdj"
    assert parsed.signals[1].codes == ["600519", "000858"]


def test_signal_set_defaults():
    ss = SignalSet()
    assert ss.schema_version == "2.0"
    assert ss.execution_key == ""
    assert ss.signal_from is None
    assert ss.signal_to is None
    assert ss.strategies_snapshot == []
    assert ss.signals == []


def test_to_dict_date_format():
    ss = SignalSet(
        execution_key="test",
        signal_from=date(2026, 7, 1),
        signal_to=date(2026, 7, 9),
        signals=[
            StrategySignal(
                strategy_id="test_id",
                strategy_name="test",
                signal_date=date(2026, 7, 5),
                codes=["000001"],
            ),
        ],
    )
    d = ss.to_dict()
    assert d["signal_from"] == "2026-07-01"
    assert d["signal_to"] == "2026-07-09"
    assert d["signals"][0]["signal_date"] == "2026-07-05"


def test_from_dict_missing_dates():
    data = {
        "execution_key": "test",
        "signals": [
            {
                "strategy_id": "test_id",
                "strategy_name": "test",
            }
        ],
    }
    ss = SignalSet.from_dict(data)
    assert ss.signal_from is None
    assert ss.signal_to is None
    assert ss.signals[0].signal_date is None


def test_signal_set_is_frozen():
    ss = SignalSet(execution_key="test")
    with pytest.raises(Exception):
        ss.execution_key = "changed"


def test_strategy_signal_is_frozen():
    s = StrategySignal(strategy_id="test", strategy_name="Test")
    with pytest.raises(Exception):
        s.strategy_id = "changed"


class TestSignalRepository:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(storage_root=Path(tmp))
            repo = SignalRepository(store)

            ss = SignalSet(
                execution_key="20260709_120000_selection",
                signal_from=date(2026, 7, 1),
                signal_to=date(2026, 7, 9),
                strategies_snapshot=[{"id": "bbi_kdj_b1", "name": "B1战法"}],
                signals=[
                    StrategySignal(
                        strategy_id="bbi_kdj_b1",
                        strategy_name="B1战法",
                        group_ids=["default"],
                        primary_group_id="default",
                        signal_date=date(2026, 7, 9),
                        codes=["000001", "600519"],
                    ),
                ],
            )

            key = repo.save(ss, "20260709_120000_selection")
            assert key is not None

            loaded = repo.load("20260709_120000_selection")
            assert loaded is not None
            assert loaded.execution_key == "20260709_120000_selection"
            assert len(loaded.signals) == 1
            assert loaded.signals[0].strategy_id == "bbi_kdj_b1"
            assert loaded.signals[0].signal_date == date(2026, 7, 9)

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(storage_root=Path(tmp))
            repo = SignalRepository(store)
            result = repo.load("nonexistent_key")
            assert result is None

    def test_save_empty_signal_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(storage_root=Path(tmp))
            repo = SignalRepository(store)

            ss = SignalSet(execution_key="empty_test")
            key = repo.save(ss, "empty_test")
            assert key is not None

            loaded = repo.load("empty_test")
            assert loaded is not None
            assert loaded.signals == []
            assert loaded.schema_version == "2.0"
