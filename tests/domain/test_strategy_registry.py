import pytest
from trendradar.domain.strategy.registry import register, get, list_all
from trendradar.domain.strategy.models import StrategyDefinition


class FakeSelector:
    definition = None


@pytest.fixture(autouse=True)
def _restore_registry():
    """Keep the module-level registry clean between tests (shared global)."""
    import trendradar.domain.strategy.registry as registry

    before = dict(registry._registry)
    yield
    registry._registry.clear()
    registry._registry.update(before)


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
