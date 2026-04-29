import {
  ArrowDownOutlined,
  ArrowLeftOutlined,
  ArrowUpOutlined,
  MinusOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
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
import { ParamsSnapshot, StrategySnapshots } from "../../components/StrategySnapshots";
import { getBacktestReport } from "../../services/backtests";
import type { BacktestReportResponse, BacktestSkip, BacktestSummary, BacktestTrade } from "../../types/backtest";
import { formatMoney, formatNumber, formatPercent, parseFiniteNumber, signedClassName } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

function formatPlainPercent(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "-";
  }
  return `${num.toFixed(2)}%`;
}

function winRateClassName(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "";
  }
  if (num >= 50) {
    return "win-rate-good";
  }
  if (num >= 30) {
    return "win-rate-mid";
  }
  return "win-rate-low";
}

function SignedValue({
  value,
  type,
}: {
  value: unknown;
  type: "money" | "percent";
}) {
  const num = parseFiniteNumber(value);
  const Icon = num === null || num === 0 ? MinusOutlined : num > 0 ? ArrowUpOutlined : ArrowDownOutlined;
  return (
    <span className={`signed-value ${signedClassName(value)}`}>
      <Icon />
      {type === "money" ? formatMoney(value) : formatPercent(value)}
    </span>
  );
}

function selectedStrategySet(selectedStrategies: string[]) {
  return new Set(selectedStrategies);
}

function totalTrades(summaries: BacktestSummary[]) {
  return summaries.reduce((sum, item) => sum + (parseFiniteNumber(item.trade_count) || 0), 0);
}

function weightedWinRate(summaries: BacktestSummary[]) {
  const trades = totalTrades(summaries);
  if (!trades) {
    return null;
  }
  const weighted = summaries.reduce(
    (sum, item) => sum + (parseFiniteNumber(item.win_rate_pct) || 0) * (parseFiniteNumber(item.trade_count) || 0),
    0,
  );
  return weighted / trades;
}

function bestTotalReturn(summaries: BacktestSummary[]) {
  if (!summaries.length) {
    return null;
  }
  return summaries.reduce((best, item) => {
    const totalReturn = parseFiniteNumber(item.total_return_pct);
    if (totalReturn === null) {
      return best;
    }
    if (best === null) {
      return totalReturn;
    }
    return Math.max(best, totalReturn);
  }, null as number | null);
}

function toggleStrategy(current: string[], strategy: string) {
  if (current.includes(strategy)) {
    return current.filter((item) => item !== strategy);
  }
  return [...current, strategy];
}

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

  const last = points[points.length - 1];
  const cumulativePoints = buildCumulativeTrendPoints(points);
  const cumulativeDomain = percentDomain(cumulativePoints.map((point) => point.cumulativeReturnPct));
  const dailyDomain = percentDomain(points.map((point) => point.dailyReturnPct));
  const barSize = Math.max(8, Math.min(28, Math.floor(140 / Math.max(points.length, 1))));

  return (
    <Card className="return-trend-card">
      <div className="equity-card-head">
        <Tag color="blue">{strategy}</Tag>
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
                <YAxis
                  domain={cumulativeDomain}
                  tick={{ fill: "#57606a", fontSize: 12 }}
                  tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
                />
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
                <YAxis
                  domain={dailyDomain}
                  tick={{ fill: "#57606a", fontSize: 12 }}
                  tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
                />
                <Tooltip content={<ReturnTrendTooltip />} />
                <Legend />
                <ReferenceLine y={0} stroke="#8c959f" strokeDasharray="4 4" label={{ value: "0%", fill: "#57606a", fontSize: 12 }} />
                <Bar
                  dataKey="dailyReturnPct"
                  name="每日收益率"
                  barSize={barSize}
                  maxBarSize={28}
                  radius={[4, 4, 0, 0]}
                >
                  {points.map((point) => (
                    <Cell
                      fill={point.dailyReturnPct >= 0 ? "#cf222e" : "#1a7f37"}
                      key={point.date}
                    />
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

function TradeReturnTrendCards({ trades }: { trades: BacktestTrade[] }) {
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

const summaryColumns: ColumnsType<BacktestSummary> = [
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
    title: "最大回撤",
    dataIndex: "max_drawdown_pct",
    key: "max_drawdown_pct",
    width: 110,
    render: (value) => <SignedValue value={value} type="percent" />,
    sorter: (a, b) => Number(a.max_drawdown_pct || 0) - Number(b.max_drawdown_pct || 0),
  },
  { title: "Sharpe", dataIndex: "sharpe", key: "sharpe", width: 90, render: (value) => formatNumber(value, 2) },
  { title: "最终现金", dataIndex: "final_cash", key: "final_cash", width: 130, render: formatMoney },
];

const tradeColumns: ColumnsType<BacktestTrade> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170 },
  { title: "代码", dataIndex: "code", key: "code", width: 100 },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  { title: "所属板块", dataIndex: "industry", key: "industry", width: 130, render: (value) => value || "-" },
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

const skipColumns: ColumnsType<BacktestSkip> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 170 },
  { title: "代码", dataIndex: "code", key: "code", width: 100 },
  { title: "名称", dataIndex: "name", key: "name", width: 110, render: (value) => value || "-" },
  { title: "所属板块", dataIndex: "industry", key: "industry", width: 130, render: (value) => value || "-" },
  { title: "信号日", dataIndex: "signal_date", key: "signal_date", width: 120 },
  { title: "买入日", dataIndex: "buy_date", key: "buy_date", width: 120 },
  { title: "阶段", dataIndex: "stage", key: "stage", width: 90 },
  { title: "原因", dataIndex: "reason", key: "reason", width: 220 },
];

export function BacktestReportPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const navigate = useNavigate();
  const { executionKey = "" } = useParams();
  const [report, setReport] = useState<BacktestReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);

  useEffect(() => {
    async function load() {
      if (!executionKey) {
        setErrorMessage("缺少回测 execution_key。");
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const payload = await getBacktestReport(executionKey);
        setReport(payload);
        setSelectedStrategies((payload.result.summary || []).map((item) => item.strategy));
        setErrorMessage("");
      } catch (error) {
        const text = error instanceof Error ? error.message : "加载回测报告失败。";
        setErrorMessage(text);
        messageApi.error(text);
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [messageApi, executionKey]);

  const run = report?.result;
  const selectedSet = useMemo(() => selectedStrategySet(selectedStrategies), [selectedStrategies]);
  const selectedSummaries = useMemo(
    () => (run?.summary || []).filter((item) => selectedSet.has(item.strategy)),
    [run?.summary, selectedSet],
  );
  const filteredTrades = useMemo(
    () => (report?.trades || []).filter((item) => selectedSet.has(item.strategy)),
    [report?.trades, selectedSet],
  );
  const filteredSkips = useMemo(
    () => (report?.skips || []).filter((item) => selectedSet.has(item.strategy)),
    [report?.skips, selectedSet],
  );
  const overviewWinRate = weightedWinRate(selectedSummaries);
  const overviewBestReturn = bestTotalReturn(selectedSummaries);
  const overviewTrades = totalTrades(selectedSummaries);

  return (
    <div className="feature-page">
      {contextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Backtest Report</div>
          <Title level={1}>回测报告</Title>
          <Paragraph className="muted-text">{run?.execution_key || executionKey}</Paragraph>
        </div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/backtests/history")}>
          返回回测
        </Button>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Card className="workbench-card result-card" loading={loading}>
        {!run ? (
          <Alert type="info" showIcon message="暂无回测报告。" />
        ) : (
          <Space direction="vertical" size={18} className="full-width">
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={6}>
                <Card className="metric-card">
                  <div className="metric-range">
                    <Text className="metric-range-title">回测区间</Text>
                    <div className="metric-range-row">
                      <Text className="metric-range-label">from</Text>
                      <Text className="metric-range-value">{run.start_date}</Text>
                    </div>
                    <div className="metric-range-row">
                      <Text className="metric-range-label">to</Text>
                      <Text className="metric-range-value">{run.end_date}</Text>
                    </div>
                  </div>
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic
                    title="最佳总收益"
                    value={overviewBestReturn === null ? "-" : formatPercent(overviewBestReturn)}
                    valueStyle={{ color: Number(overviewBestReturn || 0) >= 0 ? "#cf222e" : "#1a7f37" }}
                  />
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic
                    title="加权胜率"
                    value={formatPlainPercent(overviewWinRate)}
                    valueStyle={{
                      color:
                        Number(overviewWinRate || 0) >= 50
                          ? "#cf222e"
                          : Number(overviewWinRate || 0) >= 30
                            ? "#9a6700"
                            : "#1a7f37",
                    }}
                  />
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic title="交易数" value={overviewTrades} />
                </Card>
              </Col>
            </Row>

            <section>
              <StrategySnapshots
                strategies={run.strategy_snapshots || []}
                extra={
                  <ParamsSnapshot
                    params={run.trade_rule || {}}
                    capitalMode={run.capital_mode}
                    cashPerTrade={run.cash_per_trade}
                  />
                }
              />
            </section>

            <section>
              <div className="section-title">策略对比</div>
              <Paragraph className="muted-text">
                勾选策略后，下方交易明细、跳过记录和交易收益走势会同步过滤。当前选中 {selectedStrategies.length} 个策略。
              </Paragraph>
              <Table
                rowKey={(record) => record.strategy}
                columns={summaryColumns}
                dataSource={run.summary || []}
                pagination={false}
                rowSelection={{
                  selectedRowKeys: selectedStrategies,
                  onChange: (keys) => setSelectedStrategies(keys.map(String)),
                }}
                onRow={(record) => ({
                  onClick: (event) => {
                    const target = event.target;
                    if (target instanceof Element && target.closest(".ant-checkbox-wrapper")) {
                      return;
                    }
                    setSelectedStrategies((current) => toggleStrategy(current, record.strategy));
                  },
                })}
                rowClassName={(record) => (selectedSet.has(record.strategy) ? "selected-summary-row" : "")}
                scroll={{ x: 900 }}
                size="middle"
              />
            </section>

            <section>
              <div className="section-title">交易明细</div>
              <Table
                rowKey={(record, index) => `${record.strategy}-${record.code}-${record.signal_date}-${index}`}
                columns={tradeColumns}
                dataSource={filteredTrades}
                pagination={{ defaultPageSize: 12, showSizeChanger: true }}
                scroll={{ x: 1540 }}
                size="middle"
              />
            </section>

            {filteredSkips.length ? (
              <section>
                <div className="section-title">跳过记录</div>
                <Table
                  rowKey={(record, index) => `${record.strategy}-${record.code}-${record.signal_date}-${index}`}
                  columns={skipColumns}
                  dataSource={filteredSkips}
                  pagination={{ pageSize: 8 }}
                  scroll={{ x: 1060 }}
                  size="middle"
                />
              </section>
            ) : null}

            <section>
              <div className="section-title">交易收益走势</div>
              <Paragraph className="muted-text">
                每日收益率按卖出日统计：当天已平仓交易盈亏 / 当天已平仓交易投入成本。累计收益率按：累计已平仓盈亏 / 累计已平仓投入成本。
              </Paragraph>
              <TradeReturnTrendCards trades={filteredTrades} />
            </section>
          </Space>
        )}
      </Card>
    </div>
  );
}
