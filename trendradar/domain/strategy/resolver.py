from __future__ import annotations
import dataclasses

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

        # 执行层接线：用户参数（settings.params）覆盖注册表默认，selector 实际生效。
        # replace 产生新实例，不污染注册表共享的 definition。
        if s.get("params"):
            defn = dataclasses.replace(
                defn,
                default_params={**defn.default_params, **s["params"]},
            )
        result.append(defn)

    return result


def _find_group(groups: list[dict], gid: str) -> dict | None:
    for g in groups:
        if g["id"] == gid:
            return g
    return None


def validate_request_ids(group_defs: list[dict], request: dict | None) -> None:
    """提交时同步校验：调用方显式传入的 id 必须存在，否则 400。

    与 resolve 的静默跳过分工明确——disabled 是正常配置（resolve 阶段跳过），
    未知 id 是调用方错误（拼写/已删除），必须当场报出来，不能让任务跑完
    给个空结果还以为是"今天没信号"。
    """
    if not request:
        return

    unknown_groups = [
        gid for gid in (request.get("groups") or [])
        if _find_group(group_defs, gid) is None
    ]
    unknown_strategies = [
        sid for sid in (request.get("strategies") or [])
        if get_defn(sid) is None
    ]

    problems: list[str] = []
    if unknown_groups:
        problems.append(f"策略组不存在: {', '.join(unknown_groups)}")
    if unknown_strategies:
        problems.append(f"策略不存在: {', '.join(unknown_strategies)}")
    if problems:
        raise ValueError("；".join(problems))
