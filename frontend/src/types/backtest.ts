import type { Strategy } from "./strategy";

export interface BacktestSummary {
  strategy: string;
  trade_count: number;
  skip_count: number;
  win_rate_pct: number;
  total_return_pct: number;
  annual_return_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  final_cash: number;
  initial_cash: number;
  open_positions: number;
  capital_mode: string;
  fixed_cash_per_trade: number;
  start_date: string;
  end_date: string;
  simulation_end_date?: string;
}

export interface BacktestResult {
  execution_key: string;
  created_at?: string;
  finished_at?: string;
  start_date: string;
  end_date: string;
  strategies: string[];
  capital_mode: string;
  cash_per_trade: number;
  strategy_snapshots: Strategy[];
  status: string;
  summary: BacktestSummary[];
  object_dir_key?: string;
  signal_dir?: string | null;
  trade_rule?: Record<string, unknown>;
}

export interface BacktestTrade {
  execution_key: string;
  strategy: string;
  code: string;
  name?: string;
  industry?: string;
  signal_date: string;
  buy_date: string;
  sell_date: string;
  buy_price: number;
  sell_price: number;
  shares: number;
  buy_amount?: number;
  sell_amount?: number;
  total_cost?: number;
  total_fee?: number;
  profit: number;
  return_pct: number;
  sell_postpone_days: number;
}

export interface BacktestEquity {
  execution_key: string;
  strategy: string;
  date: string;
  cash: number;
  equity: number;
  position_count: number;
}

export interface BacktestSkip {
  execution_key: string;
  strategy: string;
  code: string;
  name?: string;
  industry?: string;
  signal_date: string;
  buy_date: string;
  stage: string;
  reason: string;
  date_ref?: string;
}

export interface BacktestArtifact {
  artifact_scope: string;
  artifact_type: string;
  storage_key: string;
  mime_type: string;
  size_bytes: number;
  checksum: string;
  created_at: string;
}

export interface BacktestResultsResponse {
  results: BacktestResult[];
}

export interface BacktestReportResponse {
  result: BacktestResult;
  artifacts: BacktestArtifact[];
  trades: BacktestTrade[];
  equity: BacktestEquity[];
  skips: BacktestSkip[];
}
