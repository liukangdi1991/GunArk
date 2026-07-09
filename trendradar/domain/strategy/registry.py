from __future__ import annotations
import json
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


def import_from_configs(configs: list[dict]) -> list[StrategySettings]:
    from trendradar.domain.strategy.models import StrategySettings
    settings = []
    for entry in configs:
        sid = _config_alias_to_id(entry.get("alias", ""))
        if sid is None or sid not in _registry:
            continue
        settings.append(StrategySettings(
            strategy_id=sid,
            enabled=entry.get("activate", True),
            params_json=json.dumps(entry.get("params", {}), ensure_ascii=False),
        ))
    return settings


_CONFIG_ALIAS_MAP = {
    "B1战法": "bbi_kdj_b1",
    "SuperB1战法": "super_b1",
    "补票战法": "bbi_short_long",
    "填坑战法": "peak_kdj",
    "上穿60放量战法": "ma60_volume_wave",
    "多空平衡选股策略": "zxdkx_balance",
    "B1战法（V2）": "perfect_b1_v2",
    "完美B1": "perfect_b1_volume_stepdown",
    "暴力K战法": "big_bullish_volume",
    "倍量多空平衡策略": "volume_spike_balance",
}


def _config_alias_to_id(alias: str) -> str | None:
    return _CONFIG_ALIAS_MAP.get(alias)
