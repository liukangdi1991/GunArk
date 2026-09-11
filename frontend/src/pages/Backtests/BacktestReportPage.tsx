import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ParamsSnapshot, StrategySnapshots } from "../../components/StrategySnapshots";
import { getBacktestReport } from "../../services/backtests";
import type { BacktestReportResponse, BacktestSummary } from "../../types/backtest";
import { formatDateRange, formatPercent, parseFiniteNumber } from "../../utils/format";
import { formatPlainPercent } from "./components/BacktestSignedValue";
import { buildSummaryColumns, openPositionColumns, skipColumns, tradeColumns } from "./components/BacktestReportTables";
import { TradeReturnTrendCards } from "./components/TradeReturnTrendCards";

const { Paragraph, Text, Title } = Typography;

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
  const filteredOpenPositions = useMemo(
    () => (report?.open_positions || []).filter((item) => selectedSet.has(item.strategy)),
    [report?.open_positions, selectedSet],
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
                    {run.selection_from || run.selection_to ? (
                      <div className="metric-range-row">
                        <Text className="metric-range-label">选股</Text>
                        <Text className="metric-range-value">
                          {formatDateRange(run.selection_from, run.selection_to)}
                          {run.selection_execution_keys?.map((k) => (
                            <Link key={k} to={`/selections/${k}`} style={{ marginLeft: 8 }}>查看选股</Link>
                          ))}
                        </Text>
                      </div>
                    ) : null}
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
                columns={buildSummaryColumns(run.capital_mode)}
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
                scroll={{ x: 1150 }}
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

            {filteredOpenPositions.length ? (
              <section>
                <div className="section-title">期末未平仓（卖不掉）</div>
                <Alert
                  className="workbench-alert"
                  type="warning"
                  showIcon
                  message={`有 ${filteredOpenPositions.length} 笔持仓到最后一个交易日仍没能卖出，按"顺延到底"处理，不在跌停价上假装成交。`}
                  description={
                    run.capital_mode === "unlimited_cash"
                      ? "这些票的名义金额还压在里面，浮动盈亏计入「未平仓盈亏」；无限资金模式不产组合收益、回撤与期末市值，也没有一笔成交可以对账。"
                      : "这些票的钱还压在里面，组合收益、回撤、期末市值都含它们的浮动盈亏，但没有一笔成交可以对账。"
                  }
                />
                <Table
                  rowKey={(record, index) => `${record.strategy}-${record.code}-${record.buy_date}-${index}`}
                  columns={openPositionColumns}
                  dataSource={filteredOpenPositions}
                  pagination={{ pageSize: 8 }}
                  scroll={{ x: 1700 }}
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
