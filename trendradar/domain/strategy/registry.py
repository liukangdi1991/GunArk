from __future__ import annotations
from trendradar.domain.strategy.models import StrategyDefinition


_registry: dict[str, StrategyDefinition] = {}


def register(defn: StrategyDefinition) -> None:
    _registry[defn.strategy_id] = defn


def get(strategy_id: str) -> StrategyDefinition | None:
    return _registry.get(strategy_id)


def list_all() -> list[StrategyDefinition]:
    return sorted(_registry.values(), key=lambda d: d.strategy_id)


def get_default_params(strategy_id: str) -> dict:
    defn = get(strategy_id)
    if defn is None:
        return {}
    return dict(defn.default_params)
