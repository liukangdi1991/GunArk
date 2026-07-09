import pytest
from trendradar.domain.strategy.resolver import resolve
from trendradar.domain.strategy.registry import register
from trendradar.domain.strategy.models import StrategyDefinition


@pytest.fixture(autouse=True)
def _setup():
    for sid, name in [("s1", "Str1"), ("s2", "Str2"), ("s3", "Str3"), ("s4", "Str4")]:
        register(StrategyDefinition(
            strategy_id=sid, name=name, description="",
            selector_class=type("Fake", (), {}),
        ))


def test_empty_request_uses_default():
    groups = [{"id": "default", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "default", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, None)
    assert [d.strategy_id for d in result] == ["s1"]


def test_request_groups():
    groups = [{"id": "g1", "enabled": True, "sort_order": 1}]
    members = [{"group_id": "g1", "strategy_id": "s2", "sort_order": 0}]
    settings = {"s2": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"]})
    assert [d.strategy_id for d in result] == ["s2"]


def test_request_strategies():
    groups = []
    members = []
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"strategies": ["s1"]})
    assert [d.strategy_id for d in result] == ["s1"]


def test_mixed_groups_and_strategies():
    groups = [{"id": "g1", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}, "s2": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"], "strategies": ["s2"]})
    assert [d.strategy_id for d in result] == ["s1", "s2"]


def test_disabled_strategy_skipped():
    groups = [{"id": "default", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "default", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": False}}
    result = resolve(groups, members, settings, None)
    assert result == []


def test_disabled_group_skipped():
    groups = [{"id": "g1", "enabled": False, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"]})
    assert result == []


def test_dedup_same_strategy_in_multiple_groups():
    groups = [
        {"id": "g1", "enabled": True, "sort_order": 0},
        {"id": "g2", "enabled": True, "sort_order": 1},
    ]
    members = [
        {"group_id": "g1", "strategy_id": "s1", "sort_order": 0},
        {"group_id": "g2", "strategy_id": "s1", "sort_order": 0},
    ]
    settings = {"s1": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1", "g2"]})
    assert [d.strategy_id for d in result] == ["s1"]


def test_direct_strategies_after_groups():
    groups = [{"id": "g1", "enabled": True, "sort_order": 0}]
    members = [{"group_id": "g1", "strategy_id": "s1", "sort_order": 0}]
    settings = {"s1": {"enabled": True}, "s2": {"enabled": True}, "s3": {"enabled": True}}
    result = resolve(groups, members, settings, {"groups": ["g1"], "strategies": ["s2", "s3"]})
    assert [d.strategy_id for d in result] == ["s1", "s2", "s3"]
