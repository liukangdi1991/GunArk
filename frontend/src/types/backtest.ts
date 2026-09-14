import type { Paged } from "./paging";
import type { Strategy } from "./strategy";

export interface BacktestSummary {
  /** 策略 id：rowKey / 筛选键，稳定不随改名变 */
  strategy: string;
  /** 中文展示名，来自策略注册表；策略已被删除时为 null，显示回落 strategy */
  strategy_name?: string | null;
  trade_count: number;
  skip_count: number;
  win_rate_pct: number;
  /** Σ盈亏 / Σ投入面额；一笔都没成交时为 null（不是 0%） */
  total_return_pct: number | null;
  realized_profit_sum: number;
  invested_notional_sum: number;
  unrealized_pnl: number;
  /**
   * 组合级净值。unlimited_cash 模式不产净值曲线（现金上限无限，没有"这个账户
   * 值多少钱"可言），此时全部为 null。
   */
  annual_return_pct: number | null;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  final_cash: number | null;
  initial_cash: number | null;
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
  selection_execution_keys?: string[];
  selection_from?: string | null;
  selection_to?: string | null;
  trade_rule?: Record<string, unknown>;
}

export interface BacktestTrade {
  execution_key: string;
  strategy: string;
  strategy_name?: string | null;
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
  strategy_name?: string | null;
  code: string;
  name?: string;
  industry?: string;
  signal_date: string;
  buy_date: string;
  stage: string;
  reason: string;
  date_ref?: string;
}

/** 回测结束时仍卖不掉的持仓：占着钱、算在净值里，但没有成交可对账。 */
export interface BacktestOpenPosition {
  execution_key: string;
  strategy: string;
  strategy_name?: string | null;
  code: string;
  name?: string;
  industry?: string;
  signal_date: string;
  buy_date: string;
  target_sell_date: string;
  shares: number;
  entry_price: number;
  entry_cost: number;
  mark_price: number;
  mark_date?: string | null;
  blocked_since?: string | null;
  blocked_reason: string;
  blocked_trading_days: number;
  unrealized_pnl: number;
  unrealized_return_pct: number;
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

/** P2：报告只回摘要 + 服务端聚合的曲线；明细走分页端点。 */
export interface BacktestReportResponse {
  result: BacktestResult;
  trend: BacktestTrendSeries[];
  counts: BacktestReportCounts;
}

/** 服务端聚合的收益曲线（P2）：口径 = 按 (策略, 卖出日) 聚合后按日累加。 */
export interface BacktestTrendPoint {
  date: string;
  dailyCost: number;
  dailyProfit: number;
  dailyReturnPct: number;
  cumulativeCost: number;
  cumulativeProfit: number;
  cumulativeReturnPct: number;
  tradeCount: number;
  winCount: number;
  lossCount: number;
}

export interface BacktestTrendSeries {
  /** 策略 id（与 summary 的筛选键一致） */
  strategy: string;
  strategy_name?: string | null;
  points: BacktestTrendPoint[];
}

export interface BacktestReportCounts {
  trade_count: number;
  skip_count: number;
  open_position_count: number;
}

export type BacktestTradesPage = Paged<BacktestTrade>;
export type BacktestSkipsPage = Paged<BacktestSkip>;
export type BacktestOpenPositionsPage = Paged<BacktestOpenPosition>;

/** 明细数据集：与三个分页端点一一对应。 */
export type BacktestDataset = "trades" | "skips" | "open-positions";
