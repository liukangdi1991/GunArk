import { requestJson } from "./apiClient";
import type { Strategy, StrategyGroup, StrategyListResponse } from "../types/strategy";

export function listStrategies(): Promise<StrategyListResponse> {
  return requestJson<StrategyListResponse>("/api/strategies");
}

export function listStrategyGroups(): Promise<StrategyGroup[]> {
  return requestJson<StrategyGroup[]>("/api/strategy-groups");
}

export function createStrategyGroup(body: { name: string; description?: string }): Promise<StrategyGroup> {
  return requestJson<StrategyGroup>("/api/strategy-groups", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** name/description/enabled/members 均为可选；members 为整组替换（按数组顺序作为组内排序）。 */
export function updateStrategyGroup(
  groupId: string,
  body: { name?: string; description?: string; enabled?: boolean; members?: string[] },
): Promise<StrategyGroup> {
  return requestJson<StrategyGroup>(
    `/api/strategy-groups/${encodeURIComponent(groupId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function deleteStrategyGroup(groupId: string): Promise<void> {
  return requestJson<void>(`/api/strategy-groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
}

/** 清空分组并恢复五个指标域默认组。 */
export function resetStrategyGroups(): Promise<StrategyGroup[]> {
  return requestJson<StrategyGroup[]>("/api/strategy-groups/reset-defaults", {
    method: "POST",
  });
}

export type { Strategy, StrategyGroup };
