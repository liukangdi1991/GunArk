import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { PagedTable } from "../../components/PagedTable";
import { StrategySnapshots } from "../../components/StrategySnapshots";
import { usePagedQuery } from "../../hooks/usePagedQuery";
import { getSelectionResult, getSelectionPicks } from "../../services/selections";
import type { SelectionPick, SelectionResultDetailResponse, SelectionSummary } from "../../types/selection";
import { formatDateRange, formatNumber } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

function summaryRowKey(record: Pick<SelectionSummary, "strategy" | "date">) {
  return `${record.strategy}-${record.date}`;
}

function toggleSummary(current: string[], key: string) {
  if (current.includes(key)) {
    return current.filter((item) => item !== key);
  }
  return [...current, key];
}

function statusColor(status?: string) {
  if (status === "success") {
    return "green";
  }
  if (status === "failed") {
    return "red";
  }
  return "blue";
}

const summaryColumns: ColumnsType<SelectionSummary> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", fixed: "left", width: 190 },
  { title: "日期", dataIndex: "date", key: "date", width: 120 },
  {
    title: "选中数量",
    dataIndex: "count",
    key: "count",
    width: 110,
    render: (value) => <Tag color={Number(value || 0) > 0 ? "blue" : "default"}>{value || 0} 只</Tag>,
    sorter: (a, b) => Number(a.count || 0) - Number(b.count || 0),
  },
  {
    title: "耗时",
    dataIndex: "elapsed_seconds",
    key: "elapsed_seconds",
    width: 100,
    render: (value) => `${formatNumber(value, 3)}s`,
  },
];

/** 服务端排序（白名单列 code/date/strategy）；行业仅展示、不参与排序。 */
const pickColumns: ColumnsType<SelectionPick> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 180, sorter: true },
  { title: "日期", dataIndex: "date", key: "date", width: 120, sorter: true },
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 110,
    sorter: true,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}`}>{record.code}</Link>
    ),
  },
  {
    title: "名称",
    dataIndex: "name",
    key: "name",
    width: 140,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}`}>{record.name}</Link>
    ),
  },
  {
    title: "所属行业",
    dataIndex: "industry",
    key: "industry",
    width: 140,
    render: (value) => value || "-",
  },
];

export function SelectionResultPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const navigate = useNavigate();
  const { executionKey = "" } = useParams();
  const [detail, setDetail] = useState<SelectionResultDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [selectedSummaryKeys, setSelectedSummaryKeys] = useState<string[]>([]);

  useEffect(() => {
    async function load() {
      if (!executionKey) {
        setErrorMessage("缺少选股 execution_key。");
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const payload = await getSelectionResult(executionKey);
        setDetail(payload);
        setSelectedSummaryKeys((payload.result.summary || []).map(summaryRowKey));
        setErrorMessage("");
      } catch (error) {
        const text = error instanceof Error ? error.message : "加载选股结果失败。";
        setErrorMessage(text);
        messageApi.error(text);
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [messageApi, executionKey]);

  const run = detail?.result;
  const counts = detail?.counts;
  const selectedSet = useMemo(() => new Set(selectedSummaryKeys), [selectedSummaryKeys]);
  const selectedSummaries = useMemo(
    () => (run?.summary || []).filter((item) => selectedSet.has(summaryRowKey(item))),
    [run?.summary, selectedSet],
  );
  const selectedStrategyNames = useMemo(
    () => Array.from(new Set(selectedSummaries.map((item) => item.strategy))),
    [selectedSummaries],
  );
  const filteredSnapshots = useMemo(
    () => (run?.strategy_snapshots || []).filter((strategy) => selectedStrategyNames.includes(strategy.name)),
    [run?.strategy_snapshots, selectedStrategyNames],
  );

  const picksQuery = usePagedQuery<SelectionPick>({
    queryKey: `${executionKey}|${selectedSummaryKeys.join(",")}`,
    initialLimit: 15,
    fetchPage: ({ offset, limit, sortBy, sortDir, signal }) => {
      if (!selectedSummaries.length) {
        // 一条摘要都没勾 = 过滤后为空（保持原有语义），不发无谓请求
        return Promise.resolve({ items: [], total: 0, offset, limit });
      }
      const strategies = Array.from(new Set(selectedSummaries.map((item) => item.strategy)));
      const dates = Array.from(new Set(selectedSummaries.map((item) => item.date)));
      return getSelectionPicks(
        executionKey,
        { offset, limit, strategies, dates, sortBy, sortDir },
        signal,
      );
    },
  });

  const multiDay = (counts?.trade_days ?? 0) > 1;

  return (
    <div className="feature-page">
      {contextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Selection Result</div>
          <Title level={1}>选股结果</Title>
          <Paragraph className="muted-text">{run?.execution_key || executionKey}</Paragraph>
        </div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/selections")}>
          返回选股
        </Button>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Card className="workbench-card result-card" loading={loading}>
        {!run ? (
          <Alert type="info" showIcon message="暂无选股结果。" />
        ) : (
          <Space direction="vertical" size={18} className="full-width">
            <Row gutter={[12, 12]}>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic title="选股日期" value={formatDateRange(run.selection_from || run.selection_date, run.selection_to || run.selection_date)} />
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic title="选中策略" value={selectedStrategyNames.length} />
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <Statistic
                    title={multiDay ? "选中信号（当前过滤）" : "选中股票（当前过滤）"}
                    value={picksQuery.total}
                    suffix={multiDay ? "条" : "只"}
                  />
                </Card>
              </Col>
              <Col xs={12} lg={6}>
                <Card className="metric-card">
                  <div className="metric-inline">
                    <Text className="metric-label">运行状态</Text>
                    <Tag color={statusColor(run.status)}>{run.status || "-"}</Tag>
                  </div>
                </Card>
              </Col>
            </Row>

            <section>
              <StrategySnapshots strategies={filteredSnapshots.length ? filteredSnapshots : run.strategy_snapshots || []} />
            </section>

            <section>
              <div className="section-title">策略摘要</div>
              <Paragraph className="muted-text">
                勾选策略日期后，下方选股明细会同步过滤。当前选中 {selectedSummaryKeys.length} 条记录
                {counts
                  ? multiDay
                    ? `；本次共 ${counts.total_picks} 条信号 · ${counts.unique_codes} 只（去重）· ${counts.trade_days} 个交易日。`
                    : `；本次共选出 ${counts.total_picks} 只。`
                  : "。"}
              </Paragraph>
              <Table
                rowKey={summaryRowKey}
                columns={summaryColumns}
                dataSource={run.summary || []}
                pagination={false}
                rowSelection={{
                  selectedRowKeys: selectedSummaryKeys,
                  onChange: (keys) => setSelectedSummaryKeys(keys.map(String)),
                }}
                onRow={(record) => ({
                  onClick: (event) => {
                    const target = event.target;
                    if (target instanceof Element && target.closest(".ant-checkbox-wrapper")) {
                      return;
                    }
                    setSelectedSummaryKeys((current) => toggleSummary(current, summaryRowKey(record)));
                  },
                })}
                rowClassName={(record) => (selectedSet.has(summaryRowKey(record)) ? "selected-summary-row" : "")}
                scroll={{ x: 520 }}
                size="middle"
              />
            </section>

            <section>
              <div className="section-title">选股明细</div>
              <PagedTable<SelectionPick>
                query={picksQuery}
                columns={pickColumns}
                rowKey={(record) => `${record.strategy}-${record.date}-${record.code}`}
                scrollX={760}
                emptyText="没有符合条件的选股明细"
              />
            </section>
          </Space>
        )}
      </Card>
    </div>
  );
}
