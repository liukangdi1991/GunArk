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


def to_strategy_ids(identifiers: list[str] | None) -> list[str] | None:
    """把「策略 id 或中文名」统一成 id（前端下拉传的是中文名）。

    未知标识符**原样保留**：交给 validate_request_ids 当场报 400，
    而不是在这里静默丢弃（静默丢弃会让「拼错一个策略」变成「跑完发现少一个」）。
    """
    if not identifiers:
        return None
    by_name = {d.name: d.strategy_id for d in list_all()}
    return [by_name.get(identifier, identifier) for identifier in identifiers]
