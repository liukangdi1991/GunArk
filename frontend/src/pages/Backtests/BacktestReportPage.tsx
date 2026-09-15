import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Collapse, Row, Space, Statistic, Table, Typography, message } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { PagedTable } from "../../components/PagedTable";
import { ParamsSnapshot, StrategySnapshots } from "../../components/StrategySnapshots";
import { usePagedQuery } from "../../hooks/usePagedQuery";
import type { PagedRequest } from "../../hooks/usePagedQuery";
import { currentSearchParams, readListParam, replaceSearchParams, writeListParam } from "../../utils/urlState";
import { getBacktestReport, getBacktestRows } from "../../services/backtests";
import type {
  BacktestDataset,
  BacktestOpenPosition,
  BacktestReportResponse,
  BacktestSkip,
  BacktestSummary,
  BacktestTrade,
} from "../../types/backtest";
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
  // URL 只当「挂载时的一次性初值」，读取也直接取地址栏（不经路由状态）。
  const initialParams = useRef(currentSearchParams());
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
        // 筛选状态优先取 URL（点进个股日 K 再返回时恢复原样），未知策略 id 忽略
        const known = new Set((payload.result.summary || []).map((item) => item.strategy));
        const fromUrl = readListParam(initialParams.current, "st")?.filter((id) => known.has(id));
        setSelectedStrategies(fromUrl?.length ? fromUrl : Array.from(known));
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
  // 明细：服务端分页（筛选/排序/切片都在后端）；勾选变化 → queryKey 变化 → 回第 1 页
  const detailQueryKey = `${executionKey}|${selectedStrategies.join(",")}`;
  const datasetQuery = useCallback(
    <T,>(dataset: BacktestDataset) =>
      ({ offset, limit, sortBy, sortDir, signal }: PagedRequest) =>
        selectedStrategies.length === 0
          ? Promise.resolve({ items: [] as T[], total: 0, offset, limit })
          : getBacktestRows<T>(
              executionKey,
              dataset,
              { offset, limit, strategies: selectedStrategies, sortBy, sortDir },
              signal,
            ),
    [executionKey, selectedStrategies],
  );
  const tradesQuery = usePagedQuery<BacktestTrade>({
    queryKey: detailQueryKey,
    initialLimit: 12,
    urlParamPrefix: "tr",
    fetchPage: datasetQuery<BacktestTrade>("trades"),
  });
  const skipsQuery = usePagedQuery<BacktestSkip>({
    queryKey: detailQueryKey,
    initialLimit: 8,
    urlParamPrefix: "sk",
    fetchPage: datasetQuery<BacktestSkip>("skips"),
  });
  const positionsQuery = usePagedQuery<BacktestOpenPosition>({
    queryKey: detailQueryKey,
    initialLimit: 8,
    urlParamPrefix: "op",
    fetchPage: datasetQuery<BacktestOpenPosition>("open-positions"),
  });

  const updateSelectedStrategies = useCallback(
    (next: string[]) => {
      setSelectedStrategies(next);
      replaceSearchParams((params) => writeListParam(params, "st", next));
    },
    [],
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
              <Collapse
                ghost
                items={[
                  {
                    key: "trade-rule",
                    label: "交易规则与参数快照（点击展开）",
                    children: (
                      <ParamsSnapshot
                        params={run.trade_rule || {}}
                        capitalMode={run.capital_mode}
                        cashPerTrade={run.cash_per_trade}
                      />
                    ),
                  },
                ]}
              />
              <StrategySnapshots strategies={run.strategy_snapshots || []} />
            </section>

            <section>
              <div className="section-title">交易收益走势</div>
              <Paragraph className="muted-text">
                每日收益率按卖出日统计：当天已平仓交易盈亏 / 当天已平仓交易投入成本。累计收益率按：累计已平仓盈亏 / 累计已平仓投入成本。
              </Paragraph>
              <TradeReturnTrendCards trend={report.trend} selectedStrategies={selectedStrategies} />
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
                  onChange: (keys) => updateSelectedStrategies(keys.map(String)),
                }}
                onRow={(record) => ({
                  onClick: (event) => {
                    const target = event.target;
                    if (target instanceof Element && target.closest(".ant-checkbox-wrapper")) {
                      return;
                    }
                    updateSelectedStrategies(toggleStrategy(selectedStrategies, record.strategy));
                  },
                })}
                rowClassName={(record) => (selectedSet.has(record.strategy) ? "selected-summary-row" : "")}
                scroll={{ x: 1150 }}
                size="middle"
              />
            </section>

            <section>
              <div className="section-title">交易明细</div>
              <PagedTable<BacktestTrade>
                query={tradesQuery}
                columns={tradeColumns}
                rowKey={(record) => `${record.strategy}-${record.code}-${record.signal_date}-${record.buy_date}`}
                scrollX={1540}
                emptyText="没有符合条件的成交明细"
              />
            </section>

            {skipsQuery.total ? (
              <section>
                <div className="section-title">跳过记录</div>
                <PagedTable<BacktestSkip>
                  query={skipsQuery}
                  columns={skipColumns}
                  rowKey={(record) => `${record.strategy}-${record.code}-${record.signal_date}`}
                  scrollX={1060}
                  emptyText="没有跳过记录"
                />
              </section>
            ) : null}

            {positionsQuery.total ? (
              <section>
                <div className="section-title">期末未平仓（卖不掉）</div>
                <Alert
                  className="workbench-alert"
                  type="warning"
                  showIcon
                  message={`有 ${positionsQuery.total} 笔持仓到最后一个交易日仍没能卖出，按"顺延到底"处理，不在跌停价上假装成交。`}
                  description={
                    run.capital_mode === "unlimited_cash"
                      ? "这些票的名义金额还压在里面，浮动盈亏计入「未平仓盈亏」；无限资金模式不产组合收益、回撤与期末市值，也没有一笔成交可以对账。"
                      : "这些票的钱还压在里面，组合收益、回撤、期末市值都含它们的浮动盈亏，但没有一笔成交可以对账。"
                  }
                />
                <PagedTable<BacktestOpenPosition>
                  query={positionsQuery}
                  columns={openPositionColumns}
                  rowKey={(record) => `${record.strategy}-${record.code}-${record.buy_date}`}
                  scrollX={1700}
                  emptyText="没有未平仓记录"
                />
              </section>
            ) : null}

          </Space>
        )}
      </Card>
    </div>
  );
}
