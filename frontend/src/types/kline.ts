// Spec: docs/superpowers/specs/2026-09-07-stock-kline-chart-design.md (v2.6)
export type KlinePeriod = "daily" | "weekly" | "monthly";
export type AdjustMode = "qfq" | "none";

export interface KlineBar {
  timestamp: number;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pre_close: number | null;
  volume: number;
  amount: number;
  zx_short: number | null;
  zx_long: number | null;
}

export interface KlineResponse {
  code: string;
  name: string;
  industry: string | null;
  period: KlinePeriod;
  adjust: AdjustMode;
  adjust_degraded: boolean;
  last_bar_date: string;
  bars: KlineBar[];
}

/** #6 个股快照（daily_basic 最新交易日；无 token/异常时数值字段为 null）。 */
export interface StockSnapshot {
  code: string;
  trade_date: string;
  circ_mv: number | null;
  total_mv: number | null;
  turnover_rate: number | null;
  pe_ttm: number | null;
  pb: number | null;
}
