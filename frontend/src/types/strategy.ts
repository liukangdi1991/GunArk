export interface Strategy {
  name: string;
  class: string;
  description: string;
  params: Record<string, unknown>;
}

export interface StrategyListResponse {
  strategies: Strategy[];
}

export interface StrategyDefinition {
  strategy_id: string;
  name: string;
  description: string;
  default_params: Record<string, unknown>;
}

export interface StrategyGroup {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
  members: StrategyGroupMember[];
}

export interface StrategyGroupMember {
  strategy_id: string;
  sort_order: number;
}

export interface StrategySettings {
  strategy_id: string;
  enabled: boolean;
  params_json: string;
}
