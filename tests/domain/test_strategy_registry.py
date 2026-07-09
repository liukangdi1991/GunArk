import pytest
from trendradar.domain.strategy.registry import register, get, list_all, import_from_configs
from trendradar.domain.strategy.models import StrategyDefinition, StrategySettings


class FakeSelector:
    definition = None


def test_register_and_get():
    defn = StrategyDefinition(
        strategy_id="test_001", name="Test", description="",
        selector_class=FakeSelector, default_params={"a": 1},
    )
    register(defn)
    assert get("test_001") is defn
    assert get("nonexistent") is None


def test_list_all_sorted():
    ids = [d.strategy_id for d in list_all()]
    assert ids == sorted(ids)


def test_import_from_configs():
    register(StrategyDefinition(
        strategy_id="bbi_kdj_b1", name="BBI KDJ B1", description="",
        selector_class=FakeSelector,
    ))
    configs = [
        {"class": "BBIKDJSelector", "alias": "B1战法", "activate": True, "params": {"j": 10}},
        {"class": "UnknownSelector", "alias": "不存在", "activate": True, "params": {}},
    ]
    settings = import_from_configs(configs)
    assert len(settings) == 1
    assert settings[0].strategy_id == "bbi_kdj_b1"
    assert settings[0].enabled is True
