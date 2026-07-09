from __future__ import annotations
from trendradar.domain.strategy.registry import get as get_defn
from trendradar.domain.strategy.models import StrategyDefinition

ResolverInput = dict  # {"groups": [...], "strategies": [...]}


def resolve(
    group_defs: list[dict],
    member_defs: list[dict],
    settings_map: dict[str, dict],
    request: dict | None = None,
) -> list[StrategyDefinition]:
    if request is None:
        request = {}

    req_groups = request.get("groups") or []
    req_strategies = request.get("strategies") or []

    if not req_groups and not req_strategies:
        req_groups = ["default"]

    group_strategy_ids: list[str] = []
    seen_in_groups: set[str] = set()
    for gid in req_groups:
        g = _find_group(group_defs, gid)
        if g is None or not g["enabled"]:
            continue
        members = [m for m in member_defs if m["group_id"] == gid]
        members.sort(key=lambda m: m["sort_order"])
        for m in members:
            if m["strategy_id"] not in seen_in_groups:
                seen_in_groups.add(m["strategy_id"])
                group_strategy_ids.append(m["strategy_id"])

    direct_strategy_ids = [
        sid for sid in req_strategies
        if sid not in seen_in_groups
    ]

    all_ids = group_strategy_ids + direct_strategy_ids

    seen: set[str] = set()
    result: list[StrategyDefinition] = []
    for sid in all_ids:
        if sid in seen:
            continue
        seen.add(sid)

        s = settings_map.get(sid, {})
        if not s.get("enabled", True):
            continue

        defn = get_defn(sid)
        if defn is None:
            continue
        result.append(defn)

    return result


def _find_group(groups: list[dict], gid: str) -> dict | None:
    for g in groups:
        if g["id"] == gid:
            return g
    return None
