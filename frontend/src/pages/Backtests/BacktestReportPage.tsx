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
import { readRememberedScroll, rememberScroll } from "../../utils/scrollMemory";
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
import {
  buildOpenPositionColumns,
  buildSummaryColumns,
  buildTradeColumns,
  skipColumns,
} from "./components/BacktestReportTables";
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
  // 查询用的策略集合（与勾选框 UI 解耦）：null = 全部策略。
  // 若把 queryKey 挂在 selectedStrategies 上，加载完成时的勾选初始化会触发 queryKey 变化
  // → 分页被重置回第 1 页，URL 恢复的 tr_o 就失效（2026-09-15 用户实测「回退回到第一页」的根因）。
  const [appliedStrategies, setAppliedStrategies] = useState<string[] | null>(() =>
    readListParam(initialParams.current, "st"),
  );

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

  // —— 滚动位置记忆（spec：回测报告 §7.2）——
  // 离开前把 .main-content 的位置写进当前历史条目；回退/前进、数据渲染完成后恢复。
  useEffect(() => {
    const container = document.querySelector<HTMLElement>(".main-content");
    if (!container) return;
    // 150ms 合并写入（trailing）：滚轮停稳后 150ms 内落盘。
    // 注意：不要在 cleanup 里 flush——卸载正是 react-router 推入新条目的时刻，
    // 此时带 URL 的 replaceState 会改写新条目，导致「返回」后渲染出错误的页面（实测踩坑）。
    let timer = 0;
    const onScroll = () => {
      if (timer) return;
      timer = window.setTimeout(() => {
        timer = 0;
        rememberScroll(Math.round(container.scrollTop));
      }, 150);
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      if (timer) window.clearTimeout(timer);
    };
  }, []);


  const run = report?.result;
  const selectedSet = useMemo(() => selectedStrategySet(selectedStrategies), [selectedStrategies]);
  const selectedSummaries = useMemo(
    () => (run?.summary || []).filter((item) => selectedSet.has(item.strategy)),
    [run?.summary, selectedSet],
  );
  // 明细：服务端分页（筛选/排序/切片都在后端）；用户改勾选 → queryKey 变化 → 回第 1 页。
  // queryKey 用 appliedStrategies（仅随用户操作变化），加载期的勾选初始化不影响分页状态。
  const detailQueryKey = `${executionKey}|${appliedStrategies ? appliedStrategies.join(",") : "(all)"}`;
  const datasetQuery = useCallback(
    <T,>(dataset: BacktestDataset) =>
      ({ offset, limit, sortBy, sortDir, signal }: PagedRequest) =>
        appliedStrategies !== null && appliedStrategies.length === 0
          ? Promise.resolve({ items: [] as T[], total: 0, offset, limit })
          : getBacktestRows<T>(
              executionKey,
              dataset,
              { offset, limit, strategies: appliedStrategies ?? undefined, sortBy, sortDir },
              signal,
            ),
    [executionKey, appliedStrategies],
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

  // 恢复时机：报告主体 + 三张明细表都就位（内容高度稳定），再恢复；350ms 后补一次校准
  // （行高/字体渲染可能二次变化）。用户若在这 350ms 内手动滚动，会被拉回一次——
  // 权衡：回退后的瞬间用户尚未开始交互，可接受。
  useEffect(() => {
    if (loading || !report) return;
    if (tradesQuery.loading || skipsQuery.loading || positionsQuery.loading) return;
    const restore = () => {
      const saved = readRememberedScroll();
      if (saved > 0) {
        document.querySelector<HTMLElement>(".main-content")?.scrollTo({ top: saved });
      }
    };
    restore();
    const timer = window.setTimeout(restore, 350);
    return () => window.clearTimeout(timer);
  }, [loading, report, tradesQuery.loading, skipsQuery.loading, positionsQuery.loading]);

  const updateSelectedStrategies = useCallback(
    (next: string[]) => {
      setSelectedStrategies(next);
      // 勾选变化 = 用户操作：查询条件随之更新（queryKey 变化 → 明细回第 1 页，符合预期）
      setAppliedStrategies(next);
      replaceSearchParams((params) => writeListParam(params, "st", next));
    },
    [],
  );
  // M8：勾选了策略子集时，个股链接带 st（个股页只显示勾选策略的买卖标记）；全选时不带
  const allStrategies = useMemo(() => (run?.summary ?? []).map((item) => item.strategy), [run]);
  const marksStrategyFilter = useMemo(
    () =>
      allStrategies.length > 0 &&
      selectedStrategies.length > 0 &&
      selectedStrategies.length < allStrategies.length
        ? selectedStrategies
        : null,
    [allStrategies, selectedStrategies],
  );
  const tradeColumns = useMemo(
    () => buildTradeColumns(marksStrategyFilter, tradesQuery.sortBy, tradesQuery.sortDir),
    [marksStrategyFilter, tradesQuery.sortBy, tradesQuery.sortDir],
  );
  const openPositionColumns = useMemo(
    () => buildOpenPositionColumns(marksStrategyFilter),
    [marksStrategyFilter],
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
