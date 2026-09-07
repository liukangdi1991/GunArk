import { Alert, Card, Space, Tag, Typography } from "antd";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestTrade } from "../../../types/backtest";
import { formatMoney, formatPercent, signedClassName } from "../../../utils/format";
import { SignedValue } from "./BacktestSignedValue";

const { Text } = Typography;

interface ReturnTrendPoint {
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
  isBaseline?: boolean;
}

function tradeCost(trade: BacktestTrade) {
  const totalCost = Number(trade.total_cost);
  if (Number.isFinite(totalCost) && totalCost > 0) {
    return totalCost;
  }
  const buyPrice = Number(trade.buy_price);
  const shares = Number(trade.shares);
  if (Number.isFinite(buyPrice) && Number.isFinite(shares) && buyPrice > 0 && shares > 0) {
    return buyPrice * shares;
  }
  return 0;
}

function buildReturnTrendPoints(trades: BacktestTrade[]): ReturnTrendPoint[] {
  const byDate = new Map<string, Omit<ReturnTrendPoint, "cumulativeCost" | "cumulativeProfit" | "cumulativeReturnPct">>();

  for (const trade of trades) {
    const date = trade.sell_date;
    if (!date) {
      continue;
    }
    const current = byDate.get(date) || {
      date,
      dailyCost: 0,
      dailyProfit: 0,
      dailyReturnPct: 0,
      tradeCount: 0,
      winCount: 0,
      lossCount: 0,
    };
    const profit = Number(trade.profit || 0);
    current.dailyCost += tradeCost(trade);
    current.dailyProfit += profit;
    current.tradeCount += 1;
    if (profit > 0) {
      current.winCount += 1;
    } else if (profit < 0) {
      current.lossCount += 1;
    }
    current.dailyReturnPct = current.dailyCost > 0 ? (current.dailyProfit / current.dailyCost) * 100 : 0;
    byDate.set(date, current);
  }

  let cumulativeCost = 0;
  let cumulativeProfit = 0;
  return Array.from(byDate.values())
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((item) => {
      cumulativeCost += item.dailyCost;
      cumulativeProfit += item.dailyProfit;
      return {
        ...item,
        cumulativeCost,
        cumulativeProfit,
        cumulativeReturnPct: cumulativeCost > 0 ? (cumulativeProfit / cumulativeCost) * 100 : 0,
      };
    });
}

function buildCumulativeTrendPoints(points: ReturnTrendPoint[]): ReturnTrendPoint[] {
  if (!points.length) {
    return [];
  }
  return [
    {
      date: "起点",
      dailyCost: 0,
      dailyProfit: 0,
      dailyReturnPct: 0,
      cumulativeCost: 0,
      cumulativeProfit: 0,
      cumulativeReturnPct: 0,
      tradeCount: 0,
      winCount: 0,
      lossCount: 0,
      isBaseline: true,
    },
    ...points,
  ];
}

function percentDomain(values: number[]): [number, number] {
  const finiteValues = values.filter((value) => Number.isFinite(value));
  const min = Math.min(0, ...finiteValues);
  const max = Math.max(0, ...finiteValues);
  if (min === 0 && max === 0) {
    return [-1, 1];
  }
  const padding = Math.max(0.5, (max - min) * 0.12);
  return [
    Number((min - padding).toFixed(2)),
    Number((max + padding).toFixed(2)),
  ];
}

function ReturnTrendTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ReturnTrendPoint }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) {
    return null;
  }

  if (point.isBaseline) {
    return (
      <div className="return-tooltip">
        <div className="return-tooltip-title">收益起点</div>
        <div>累计收益率：{formatPercent(0)}</div>
      </div>
    );
  }

  return (
    <div className="return-tooltip">
      <div className="return-tooltip-title">{point.date}</div>
      <div>当日交易：{point.tradeCount} 笔，盈利 {point.winCount} 笔，亏损 {point.lossCount} 笔</div>
      <div>当日投入：{formatMoney(point.dailyCost)}</div>
      <div>
        当日盈亏：
        <span className={signedClassName(point.dailyProfit)}>{formatMoney(point.dailyProfit)}</span>
      </div>
      <div>
        每日收益率：
        <span className={signedClassName(point.dailyReturnPct)}>{formatPercent(point.dailyReturnPct)}</span>
      </div>
      <div>累计投入：{formatMoney(point.cumulativeCost)}</div>
      <div>
        累计盈亏：
        <span className={signedClassName(point.cumulativeProfit)}>{formatMoney(point.cumulativeProfit)}</span>
      </div>
      <div>
        累计收益率：
        <span className={signedClassName(point.cumulativeReturnPct)}>{formatPercent(point.cumulativeReturnPct)}</span>
      </div>
    </div>
  );
}

function TradeReturnTrendCard({
  strategy,
  trades,
}: {
  strategy: string;
  trades: BacktestTrade[];
}) {
  const points = buildReturnTrendPoints(trades);
  if (!points.length) {
    return null;
  }

  const label = trades.find((trade) => trade.strategy_name)?.strategy_name || strategy;
  const last = points[points.length - 1];
  const cumulativePoints = buildCumulativeTrendPoints(points);
  const cumulativeDomain = percentDomain(cumulativePoints.map((point) => point.cumulativeReturnPct));
  const dailyDomain = percentDomain(points.map((point) => point.dailyReturnPct));
  const barSize = Math.max(8, Math.min(28, Math.floor(140 / Math.max(points.length, 1))));

  return (
    <Card className="return-trend-card">
      <div className="equity-card-head">
        <Tag color="blue">{label}</Tag>
        <Text className="muted-text">按卖出日统计已平仓交易收益</Text>
      </div>
      <div className="equity-card-metrics">
        <div>
          <Text className="equity-metric-label">累计投入</Text>
          <Text className="equity-metric-value">{formatMoney(last.cumulativeCost)}</Text>
        </div>
        <div>
          <Text className="equity-metric-label">累计盈亏</Text>
          <SignedValue value={last.cumulativeProfit} type="money" />
        </div>
        <div>
          <Text className="equity-metric-label">累计收益率</Text>
          <SignedValue value={last.cumulativeReturnPct} type="percent" />
        </div>
        <div>
          <Text className="equity-metric-label">平仓天数</Text>
          <Text className="equity-metric-value">{points.length}</Text>
        </div>
      </div>
      <div className="return-chart-stack">
        <div className="return-chart-block">
          <div className="return-chart-title">累计收益率</div>
          <div className="return-chart">
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={cumulativePoints} margin={{ top: 14, right: 18, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="#eaeef2" strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fill: "#57606a", fontSize: 12 }} />
                <YAxis domain={cumulativeDomain} tick={{ fill: "#57606a", fontSize: 12 }} tickFormatter={(value) => `${Number(value).toFixed(1)}%`} />
                <Tooltip content={<ReturnTrendTooltip />} />
                <Legend />
                <ReferenceLine y={0} stroke="#8c959f" strokeDasharray="4 4" label={{ value: "0%", fill: "#57606a", fontSize: 12 }} />
                <Line
                  type="monotone"
                  dataKey="cumulativeReturnPct"
                  name="累计收益率"
                  stroke="#0969da"
                  strokeWidth={2.5}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="return-chart-block">
          <div className="return-chart-title">每日收益率</div>
          <div className="return-chart return-chart-daily">
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={points} margin={{ top: 14, right: 18, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="#eaeef2" strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fill: "#57606a", fontSize: 12 }} />
                <YAxis domain={dailyDomain} tick={{ fill: "#57606a", fontSize: 12 }} tickFormatter={(value) => `${Number(value).toFixed(1)}%`} />
                <Tooltip content={<ReturnTrendTooltip />} />
                <Legend />
                <ReferenceLine y={0} stroke="#8c959f" strokeDasharray="4 4" label={{ value: "0%", fill: "#57606a", fontSize: 12 }} />
                <Bar dataKey="dailyReturnPct" name="每日收益率" barSize={barSize} maxBarSize={28} radius={[4, 4, 0, 0]}>
                  {points.map((point) => (
                    <Cell fill={point.dailyReturnPct >= 0 ? "#cf222e" : "#1a7f37"} key={point.date} />
                  ))}
                </Bar>
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </Card>
  );
}

export function TradeReturnTrendCards({ trades }: { trades: BacktestTrade[] }) {
  if (!trades.length) {
    return <Alert type="info" showIcon message="暂无已平仓交易，无法生成交易收益走势。" />;
  }

  const grouped = trades.reduce((map, trade) => {
    const items = map.get(trade.strategy) || [];
    items.push(trade);
    map.set(trade.strategy, items);
    return map;
  }, new Map<string, BacktestTrade[]>());

  return (
    <Space direction="vertical" size={12} className="equity-card-list">
      <Space wrap size={[8, 8]} className="equity-legend">
        <Tag className="market-up">红色柱：当日已平仓盈利</Tag>
        <Tag className="market-down">绿色柱：当日已平仓亏损</Tag>
        <Tag color="blue">蓝线：累计收益率</Tag>
      </Space>
      {Array.from(grouped.entries()).map(([strategy, items]) => (
        <TradeReturnTrendCard key={strategy} strategy={strategy} trades={items} />
      ))}
    </Space>
  );
}
