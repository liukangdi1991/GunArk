import { requestJson } from "./apiClient";
import type { Strategy, StrategyGroup, StrategyListResponse } from "../types/strategy";

export function listStrategies(): Promise<StrategyListResponse> {
  return requestJson<StrategyListResponse>("/api/strategies");
}

export function listStrategyGroups(): Promise<StrategyGroup[]> {
  return requestJson<StrategyGroup[]>("/api/strategy-groups");
}

export type { Strategy, StrategyGroup };
