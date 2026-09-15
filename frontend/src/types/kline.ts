// Spec: docs/superpowers/specs/2026-09-07-stock-kline-chart-design.md (v2.6)
import type { BacktestMark } from "./backtest";

export type KlinePeriod = "daily" | "weekly" | "monthly";
export type AdjustMode = "qfq" | "none";

/** 挂在 bar 上的回测买卖标记（由 utils/backtestMarks.attachMarks 写入）。 */
export interface BarMark {
  /** 显示文本：`B`、`S`、`B×2`、`S×3` */
  label: string;
  count: number;
  /** 画图用的 y 值：买入取 bar.low、卖出取 bar.high */
  value: number | null;
  items: BacktestMark[];
}

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
  mt: number | null;
  mt_prev: number | null;
  mt_color: string | null;
  xpsd_short: number | null;
  xpsd_mid: number | null;
  xpsd_midlong: number | null;
  xpsd_long: number | null;
  xpsig_zero: number | null;
  xpsig_w20: number | null;
  xpsig_xlong: number | null;
  xpsig_xmid: number | null;
  /** 回测买卖标记（仅当 URL 带 bt 时由 attachMarks 写入；未标记的 bar 为 null/缺省） */
  bt_buy?: BarMark | null;
  bt_sell?: BarMark | null;
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
