export interface Strategy {
  name: string;
  class: string;
  description: string;
  params: Record<string, unknown>;
}

export interface StrategyListResponse {
  strategies: Strategy[];
}
