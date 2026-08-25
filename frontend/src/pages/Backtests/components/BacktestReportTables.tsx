import type { ColumnsType } from "antd/es/table";
import type { BacktestSkip, BacktestSummary, BacktestTrade } from "../../../types/backtest";
import { formatMoney, formatNumber } from "../../../utils/format";
import { formatPlainPercent, SignedValue, winRateClassName } from "./BacktestSignedValue";

function compareText(a: unknown, b: unknown) {
  return String(a || "").localeCompare(String(b || ""), "zh-Hans-CN");
}

export const summaryColumns: ColumnsType<BacktestSummary> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", fixed: "left", width: 180 },
  { title: "交易数", dataIndex: "trade_count", key: "trade_count", width: 90, sorter: (a, b) => Number(a.trade_count || 0) - Number(b.trade_count || 0) },
  { title: "跳过数", dataIndex: "skip_count", key: "skip_count", width: 90, sorter: (a, b) => Number(a.skip_count || 0) - Number(b.skip_count || 0) },
  {
    title: "胜率",
    dataIndex: "win_rate_pct",
    key: "win_rate_pct",
    width: 100,
    render: (value) => <span className={`win-rate-pill ${winRateClassName(value)}`}>{formatPlainPercent(value)}</span>,
    sorter: (a, b) => Number(a.win_rate_pct || 0) - Number(b.win_rate_pct || 0),
  },
  {
    title: "总收益",
    dataIndex: "total_return_pct",
    key: "total_return_pct",
    width: 110,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.total_return_pct || 0) - Number(b.total_return_pct || 0),
  },
  {
    title: "最大回撤（组合）",
    dataIndex: "max_drawdown_pct",
    key: "max_drawdown_pct",
    width: 130,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.max_drawdown_pct || 0) - Number(b.max_drawdown_pct || 0),
  },
  { title: "Sharpe（组合）", dataIndex: "sharpe", key: "sharpe", width: 110, render: (value) => formatNumber(value, 2) },
  { title: "最终现金（组合）", dataIndex: "final_cash", key: "final_cash", width: 140, render: formatMoney },
];

export const tradeColumns: ColumnsType<BacktestTrade> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170 },
  { title: "代码", dataIndex: "code", key: "code", width: 100 },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  {
    title: "所属板块",
    dataIndex: "industry",
    key: "industry",
    width: 130,
    render: (value) => value || "-",
    sorter: (a, b) => compareText(a.industry, b.industry),
  },
  { title: "信号日", dataIndex: "signal_date", key: "signal_date", width: 120 },
  {
    title: "盈亏",
    dataIndex: "profit",
    key: "profit",
    width: 120,
    render: (value) => <SignedValue value={value} type="money" />,
    sorter: (a, b) => Number(a.profit || 0) - Number(b.profit || 0),
  },
  {
    title: "收益率",
    dataIndex: "return_pct",
    key: "return_pct",
    width: 110,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.return_pct || 0) - Number(b.return_pct || 0),
  },
  { title: "买入日", dataIndex: "buy_date", key: "buy_date", width: 120 },
  { title: "买入价", dataIndex: "buy_price", key: "buy_price", width: 100, render: (value) => formatNumber(value, 3) },
  { title: "卖出日", dataIndex: "sell_date", key: "sell_date", width: 120 },
  { title: "卖出价", dataIndex: "sell_price", key: "sell_price", width: 100, render: (value) => formatNumber(value, 3) },
  { title: "股数", dataIndex: "shares", key: "shares", width: 90 },
  { title: "延期", dataIndex: "sell_postpone_days", key: "sell_postpone_days", width: 80 },
];

export const skipColumns: ColumnsType<BacktestSkip> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170 },
  { title: "代码", dataIndex: "code", key: "code", width: 100 },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  {
    title: "所属板块",
    dataIndex: "industry",
    key: "industry",
    width: 130,
    render: (value) => value || "-",
    sorter: (a, b) => compareText(a.industry, b.industry),
  },
  { title: "信号日", dataIndex: "signal_date", key: "signal_date", width: 120 },
  { title: "买入日", dataIndex: "buy_date", key: "buy_date", width: 120 },
  { title: "阶段", dataIndex: "stage", key: "stage", width: 90 },
  { title: "原因", dataIndex: "reason", key: "reason", width: 220 },
];
