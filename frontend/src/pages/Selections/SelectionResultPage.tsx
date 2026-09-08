import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { StrategySnapshots } from "../../components/StrategySnapshots";
import { getSelectionResult } from "../../services/selections";
import type { SelectionPick, SelectionResultDetailResponse, SelectionSummary } from "../../types/selection";
import { formatDateRange, formatNumber } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

function compareText(a: unknown, b: unknown) {
  return String(a || "").localeCompare(String(b || ""), "zh-Hans-CN");
}

function totalPickCount(detail?: SelectionResultDetailResponse | null) {
  return (detail?.result.summary || []).reduce((sum, item) => sum + Number(item.count || 0), 0);
}

function summaryRowKey(record: Pick<SelectionSummary, "strategy" | "date">) {
  return `${record.strategy}-${record.date}`;
}

function selectedSummarySet(selectedKeys: string[]) {
  return new Set(selectedKeys);
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

const pickColumns: ColumnsType<SelectionPick> = [
  { title: "策略", dataIndex: "strategy", key: "strategy", width: 180 },
  { title: "日期", dataIndex: "date", key: "date", width: 120 },
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 110,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.date}`}>{record.code}</Link>
    ),
  },
  {
    title: "名称",
    dataIndex: "name",
    key: "name",
    width: 140,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.date}`}>{record.name}</Link>
    ),
  },
  {
    title: "所属板块",
    dataIndex: "industry",
    key: "industry",
    width: 140,
    render: (value) => value || "-",
    sorter: (a, b) => compareText(a.industry, b.industry),
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
  const selectedSet = useMemo(() => selectedSummarySet(selectedSummaryKeys), [selectedSummaryKeys]);
  const selectedSummaries = useMemo(
    () => (run?.summary || []).filter((item) => selectedSet.has(summaryRowKey(item))),
    [run?.summary, selectedSet],
  );
  const filteredPicks = useMemo(
    () => (detail?.picks || []).filter((item) => selectedSet.has(summaryRowKey(item))),
    [detail?.picks, selectedSet],
  );
  const selectedStrategyNames = useMemo(
    () => Array.from(new Set(selectedSummaries.map((item) => item.strategy))),
    [selectedSummaries],
  );
  const filteredSnapshots = useMemo(
    () => (run?.strategy_snapshots || []).filter((strategy) => selectedStrategyNames.includes(strategy.name)),
    [run?.strategy_snapshots, selectedStrategyNames],
  );

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
                  <Statistic title="选中股票" value={filteredPicks.length} suffix="只" />
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
                勾选策略日期后，下方选股明细会同步过滤。当前选中 {selectedSummaryKeys.length} 条记录。
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
              <Table
                rowKey={(record) => `${record.strategy}-${record.date}-${record.code}`}
                columns={pickColumns}
                dataSource={filteredPicks}
                pagination={{ pageSize: 15, showSizeChanger: true }}
                scroll={{ x: 760 }}
                size="middle"
              />
            </section>
          </Space>
        )}
      </Card>
    </div>
  );
}
