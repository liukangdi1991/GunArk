import pytest
from trendradar.domain.strategy.resolver import resolve, validate_request_ids
from trendradar.domain.strategy.registry import register
from trendradar.domain.strategy.models import StrategyDefinition


@pytest.fixture(autouse=True)
def _setup():
    import trendradar.domain.strategy.registry as registry

    before = dict(registry._registry)
    for sid, name in [("s1", "Str1"), ("s2", "Str2"), ("s3", "Str3"), ("s4", "Str4")]:
        register(StrategyDefinition(
            strategy_id=sid, name=name, description="",
            selector_class=type("Fake", (), {}),
        ))
    yield
    registry._registry.clear()
    registry._registry.update(before)


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


class TestValidateRequestIds:
    """提交时同步校验：未知 id 是调用方错误（400），disabled 是正常配置（不报错）。"""

    GROUPS = [
        {"id": "g1", "enabled": True, "sort_order": 0},
        {"id": "g_off", "enabled": False, "sort_order": 1},
    ]

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="no_such"):
            validate_request_ids(self.GROUPS, {"strategies": ["s1", "no_such"]})

    def test_unknown_group_raises(self):
        with pytest.raises(ValueError, match="ghost_group"):
            validate_request_ids(self.GROUPS, {"groups": ["g1", "ghost_group"]})

    def test_disabled_strategy_is_not_an_error(self):
        # s1 存在但被禁用 → 校验通过（resolve 阶段才静默跳过）
        validate_request_ids(self.GROUPS, {"strategies": ["s1"]})

    def test_disabled_group_is_not_an_error(self):
        validate_request_ids(self.GROUPS, {"groups": ["g_off"]})

    def test_all_known_passes(self):
        validate_request_ids(self.GROUPS, {"groups": ["g1"], "strategies": ["s2", "s3"]})

    def test_empty_request_passes(self):
        validate_request_ids(self.GROUPS, {})
        validate_request_ids(self.GROUPS, None)

    def test_error_lists_every_unknown_id(self):
        with pytest.raises(ValueError) as ei:
            validate_request_ids(self.GROUPS, {"strategies": ["bad1", "s1", "bad2"]})
        assert "bad1" in str(ei.value) and "bad2" in str(ei.value)
