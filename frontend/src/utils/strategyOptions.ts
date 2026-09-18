import type { Strategy, StrategyGroup } from "../types/strategy";

export interface StrategyOption {
  label: string;
  value: string;
}

export interface GroupedStrategyOption {
  label: string;
  options: StrategyOption[];
}

/** 下拉的 value 契约是策略中文名（提交后由后端 to_strategy_ids 统一成 id）。 */

/**
 * 按指标域组构建下拉项：组按 sort_order、组内按成员 sort_order；
 * 未入组的策略落「其他」桶；无组数据（接口失败或用户清空）回落为单个平铺组。
 */
export function strategySelectOptions(
  strategies: Strategy[],
  groups: StrategyGroup[] | null,
): GroupedStrategyOption[] {
  const byId = new Map(strategies.map((s) => [s.strategy_id, s]));
  if (!groups || groups.length === 0) {
    return [
      {
        label: "全部策略",
        options: strategies
          .map((s) => ({ label: s.name, value: s.name }))
          .sort((a, b) => a.label.localeCompare(b.label, "zh-Hans-CN")),
      },
    ];
  }
  const groupedNames = new Set<string>();
  const grouped: GroupedStrategyOption[] = [];
  for (const group of groups) {
    const options: StrategyOption[] = [];
    for (const member of group.members) {
      const strategy = byId.get(member.strategy_id);
      if (!strategy) continue;
      groupedNames.add(strategy.name);
      options.push({ label: strategy.name, value: strategy.name });
    }
    if (options.length) {
      grouped.push({ label: group.name, options });
    }
  }
  const rest = strategies
    .filter((s) => !groupedNames.has(s.name))
    .map((s) => ({ label: s.name, value: s.name }))
    .sort((a, b) => a.label.localeCompare(b.label, "zh-Hans-CN"));
  if (rest.length) {
    grouped.push({ label: "其他", options: rest });
  }
  return grouped;
}
