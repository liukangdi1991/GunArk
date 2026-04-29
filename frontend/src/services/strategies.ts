import { requestJson } from "./apiClient";
import type { StrategyListResponse } from "../types/strategy";

export function listStrategies(): Promise<StrategyListResponse> {
  return requestJson<StrategyListResponse>("/api/strategies");
}
