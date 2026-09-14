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
import type { BacktestTrendPoint, BacktestTrendSeries } from "../../../types/backtest";
import { formatMoney, formatPercent, signedClassName } from "../../../utils/format";
import { SignedValue } from "./BacktestSignedValue";

const { Text } = Typography;

/** 服务端聚合出的点 + 前端插入的基线点（"起点"，纯展示）。 */
type ReturnTrendPoint = BacktestTrendPoint & { isBaseline?: boolean };

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

function TradeReturnTrendCard({ series }: { series: BacktestTrendSeries }) {
  const points = series.points;
  if (!points.length) {
    return null;
  }

  const label = series.strategy_name || series.strategy;
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

export function TradeReturnTrendCards({
  trend,
  selectedStrategies,
}: {
  trend: BacktestTrendSeries[];
  selectedStrategies: string[];
}) {
  const series = trend.filter((item) => selectedStrategies.includes(item.strategy));
  if (!series.length) {
    return <Alert type="info" showIcon message="暂无已平仓交易，无法生成交易收益走势。" />;
  }

  return (
    <Space direction="vertical" size={12} className="equity-card-list">
      <Space wrap size={[8, 8]} className="equity-legend">
        <Tag className="market-up">红色柱：当日已平仓盈利</Tag>
        <Tag className="market-down">绿色柱：当日已平仓亏损</Tag>
        <Tag color="blue">蓝线：累计收益率</Tag>
      </Space>
      {series.map((item) => (
        <TradeReturnTrendCard key={item.strategy} series={item} />
      ))}
    </Space>
  );
}
