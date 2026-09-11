import { Typography } from "antd";
import { Link } from "react-router-dom";
import type { ColumnsType } from "antd/es/table";
import type {
  BacktestOpenPosition,
  BacktestSkip,
  BacktestSummary,
  BacktestTrade,
} from "../../../types/backtest";
import { formatMoney, formatNumber } from "../../../utils/format";
import { formatPlainPercent, SignedValue, winRateClassName } from "./BacktestSignedValue";

const { Text } = Typography;

function compareText(a: unknown, b: unknown) {
  return String(a || "").localeCompare(String(b || ""), "zh-Hans-CN");
}

/** 策略列显示中文名；`strategy`（id）继续当 rowKey 与筛选键，不随改名漂移。 */
function strategyLabel(record: { strategy: string; strategy_name?: string | null }) {
  return record.strategy_name || record.strategy;
}

const summaryBaseColumns: ColumnsType<BacktestSummary> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", fixed: "left", width: 180, render: (_, record) => strategyLabel(record) },
  { title: "交易数", dataIndex: "trade_count", key: "trade_count", width: 90, sorter: (a, b) => Number(a.trade_count || 0) - Number(b.trade_count || 0) },
  { title: "跳过数", dataIndex: "skip_count", key: "skip_count", width: 90, sorter: (a, b) => Number(a.skip_count || 0) - Number(b.skip_count || 0) },
  {
    title: "未平仓",
    dataIndex: "open_positions",
    key: "open_positions",
    width: 90,
    render: (value) => (Number(value || 0) > 0 ? <Text strong>{Number(value)}</Text> : "0"),
    sorter: (a, b) => Number(a.open_positions || 0) - Number(b.open_positions || 0),
  },
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
];

const summaryMoneyColumns: ColumnsType<BacktestSummary> = [
  {
    title: "累计盈亏",
    dataIndex: "realized_profit_sum",
    key: "realized_profit_sum",
    width: 130,
    render: (value) => <SignedValue value={value} type="money" />,
    sorter: (a, b) => Number(a.realized_profit_sum || 0) - Number(b.realized_profit_sum || 0),
  },
  {
    title: "累计投入",
    dataIndex: "invested_notional_sum",
    key: "invested_notional_sum",
    width: 130,
    render: formatMoney,
    sorter: (a, b) => Number(a.invested_notional_sum || 0) - Number(b.invested_notional_sum || 0),
  },
  {
    title: "未平仓浮动盈亏",
    dataIndex: "unrealized_pnl",
    key: "unrealized_pnl",
    width: 140,
    render: (value) => <SignedValue value={value} type="money" />,
    sorter: (a, b) => Number(a.unrealized_pnl || 0) - Number(b.unrealized_pnl || 0),
  },
];

const summaryNavColumns: ColumnsType<BacktestSummary> = [
  {
    title: "最大回撤（组合）",
    dataIndex: "max_drawdown_pct",
    key: "max_drawdown_pct",
    width: 130,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.max_drawdown_pct || 0) - Number(b.max_drawdown_pct || 0),
  },
  { title: "Sharpe（组合）", dataIndex: "sharpe", key: "sharpe", width: 110, render: (value) => formatNumber(value, 2) },
  { title: "期末市值（组合）", dataIndex: "final_cash", key: "final_cash", width: 140, render: formatMoney },
];

/**
 * 汇总表按资金模式分支：unlimited_cash 不产净值曲线，组合级那几列在它那里是
 * N-A（后端返回 null），列留在表里只是一列"-"——真数在净值模式那边。
 */
export function buildSummaryColumns(capitalMode?: string): ColumnsType<BacktestSummary> {
  const tracksNav = capitalMode !== "unlimited_cash";
  return [...summaryBaseColumns, ...summaryMoneyColumns, ...(tracksNav ? summaryNavColumns : [])];
}

export const tradeColumns: ColumnsType<BacktestTrade> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170, render: (_, record) => strategyLabel(record) },
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}`}>{record.code}</Link>
    ),
  },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  {
    title: "所属行业",
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
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170, render: (_, record) => strategyLabel(record) },
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}`}>{record.code}</Link>
    ),
  },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  {
    title: "所属行业",
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

export const openPositionColumns: ColumnsType<BacktestOpenPosition> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170, render: (_, record) => strategyLabel(record) },
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}`}>{record.code}</Link>
    ),
  },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  {
    title: "所属行业",
    dataIndex: "industry",
    key: "industry",
    width: 130,
    render: (value) => value || "-",
    sorter: (a, b) => compareText(a.industry, b.industry),
  },
  { title: "买入日", dataIndex: "buy_date", key: "buy_date", width: 120 },
  { title: "买入价", dataIndex: "entry_price", key: "entry_price", width: 100, render: (value) => formatNumber(value, 3) },
  { title: "股数", dataIndex: "shares", key: "shares", width: 90 },
  { title: "计划卖出日", dataIndex: "target_sell_date", key: "target_sell_date", width: 120 },
  {
    title: "顺延起始",
    dataIndex: "blocked_since",
    key: "blocked_since",
    width: 120,
    render: (value) => value || "-",
  },
  {
    title: "已顺延交易日",
    dataIndex: "blocked_trading_days",
    key: "blocked_trading_days",
    width: 120,
    sorter: (a, b) => Number(a.blocked_trading_days || 0) - Number(b.blocked_trading_days || 0),
  },
  { title: "顺延原因", dataIndex: "blocked_reason", key: "blocked_reason", width: 130 },
  {
    title: "标记价",
    dataIndex: "mark_price",
    key: "mark_price",
    width: 130,
    render: (value, record) => (
      <span>
        {formatNumber(value, 3)}
        {record.mark_date ? <Text className="muted-text">（{record.mark_date}）</Text> : null}
      </span>
    ),
    sorter: (a, b) => Number(a.mark_price || 0) - Number(b.mark_price || 0),
  },
  {
    title: "未实现盈亏",
    dataIndex: "unrealized_pnl",
    key: "unrealized_pnl",
    width: 120,
    render: (value) => <SignedValue value={value} type="money" />,
    sorter: (a, b) => Number(a.unrealized_pnl || 0) - Number(b.unrealized_pnl || 0),
  },
  {
    title: "未实现收益率",
    dataIndex: "unrealized_return_pct",
    key: "unrealized_return_pct",
    width: 130,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.unrealized_return_pct || 0) - Number(b.unrealized_return_pct || 0),
  },
];
