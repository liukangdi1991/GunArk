import { CalendarOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, DatePicker, Form, Row, Select, Space, Statistic, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EditableHistoryCard } from "../../components/EditableHistoryCard";
import { submitExecution } from "../../services/executions";
import { listTradingDates } from "../../services/marketData";
import { deleteSelectionResults, listSelectionResults } from "../../services/selections";
import { listStrategies } from "../../services/strategies";
import type { ExecutionRequestType } from "../../types/execution";
import type { SelectionResult } from "../../types/selection";
import type { Strategy } from "../../types/strategy";
import { makeDisabledNonTradingDate, formatPickerDate, firstTradingDateOfLatestMonth, latestTradingDate, toDayjs } from "../../utils/date";
import { compactStrategyNames, formatDateRange } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

type SubmitKey = "latest" | "single" | "batch";

interface SelectionFormValues {
  strategies?: string[];
  boards?: string[];
  date?: Dayjs;
  from?: Dayjs;
  to?: Dayjs;
}

const BOARD_OPTIONS = ["主板", "创业板", "科创板", "北交所"].map((b) => ({ label: b, value: b }));

function selectedStrategies(values?: string[]) {
  return values && values.length ? values : null;
}

function totalPickCount(result?: SelectionResult | null) {
  return (result?.summary || []).reduce((sum, item) => sum + Number(item.count || 0), 0);
}

export function SelectionWorkspacePage() {
  const [messageApi, contextHolder] = message.useMessage();
  const [singleForm] = Form.useForm<SelectionFormValues>();
  const [batchForm] = Form.useForm<SelectionFormValues>();
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [runs, setRuns] = useState<SelectionResult[]>([]);
  const [tradingDates, setTradingDates] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [runsLoading, setRunsLoading] = useState(false);
  const [submitting, setSubmitting] = useState<SubmitKey | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [editingHistory, setEditingHistory] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [errorMessage, setErrorMessage] = useState("");

  const strategyOptions = useMemo(
    () => strategies
      .map((strategy) => ({ label: strategy.name, value: strategy.name }))
      .sort((a, b) => a.label.localeCompare(b.label, "zh-Hans-CN")),
    [strategies],
  );
  const disabledNonTradingDate = useMemo(
    () => makeDisabledNonTradingDate(tradingDates),
    [tradingDates],
  );
  const latestDate = useMemo(() => latestTradingDate(tradingDates), [tradingDates]);
  const monthStartDate = useMemo(() => firstTradingDateOfLatestMonth(tradingDates), [tradingDates]);

  async function refreshRuns() {
    setRunsLoading(true);
    try {
      const payload = await listSelectionResults();
      setRuns(payload.results || []);
      setSelectedRunIds((current) => current.filter((runId) => (payload.results || []).some((run) => run.execution_key === runId)));
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载选股历史失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setRunsLoading(false);
    }
  }

  async function loadPage() {
    setLoading(true);
    try {
      const [strategyPayload, runsPayload, tradingDatePayload] = await Promise.all([
        listStrategies(),
        listSelectionResults(),
        listTradingDates(),
      ]);
      const dates = tradingDatePayload.dates || [];
      setStrategies(strategyPayload.strategies || []);
      setRuns(runsPayload.results || []);
      setSelectedRunIds([]);
      setTradingDates(dates);
      singleForm.setFieldsValue({ date: toDayjs(latestTradingDate(dates)) });
      batchForm.setFieldsValue({
        from: toDayjs(firstTradingDateOfLatestMonth(dates)),
        to: toDayjs(latestTradingDate(dates)),
      });
      setErrorMessage("");
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载选股功能失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setLoading(false);
    }
  }

  async function deleteRuns(runIds?: string[]) {
    setDeleting(true);
    try {
      const result = await deleteSelectionResults(runIds);
      messageApi.success(`已删除 ${result.deleted} 条选股历史`);
      if (result.file_errors?.length) {
        messageApi.warning(`有 ${result.file_errors.length} 个本地文件未能删除：${result.file_errors[0]}`);
      }
      setSelectedRunIds([]);
      if (!runIds) {
        setEditingHistory(false);
      }
      await refreshRuns();
    } catch (error) {
      const text = error instanceof Error ? error.message : "删除选股历史失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setDeleting(false);
    }
  }

  function toggleRun(runId: string, checked: boolean) {
    setSelectedRunIds((current) => {
      if (checked) {
        return current.includes(runId) ? current : [...current, runId];
      }
      return current.filter((item) => item !== runId);
    });
  }

  async function runSelection(
    key: SubmitKey,
    type: ExecutionRequestType,
    values: SelectionFormValues,
  ) {
    setSubmitting(key);
    try {
      const params: Record<string, unknown> = {
        strategies: selectedStrategies(values.strategies),
      };
      if (type === "selection_single") {
        params.date = formatPickerDate(values.date);
      }
      if (type === "selection_batch") {
        params.from = formatPickerDate(values.from);
        params.to = formatPickerDate(values.to);
      }
      if (values.boards?.length) {
        params.boards = values.boards;
      }
      const execution = await submitExecution({ type, params });
      window.location.href = execution.console_url;
    } catch (error) {
      const text = error instanceof Error ? error.message : "提交选股任务失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setSubmitting(null);
    }
  }

  useEffect(() => {
    void loadPage();
  }, []);

  return (
    <div className="feature-page">
      {contextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Selection</div>
          <Title level={1}>选股</Title>
          <Paragraph className="muted-text">执行最新交易日、指定交易日或区间批量选股。结果会在独立页面查看。</Paragraph>
        </div>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Row gutter={[12, 12]} className="workspace-metrics">
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="最新交易日" value={latestDate || "-"} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="可用策略" value={strategies.length} suffix="个" />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="交易日样本" value={tradingDates.length} suffix="天" />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={runsLoading || loading}>
            <Statistic title="历史任务" value={runs.length} suffix="次" />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card className="workbench-card" loading={loading} title="执行选股">
            <div className="action-stack">
              <Card
                className="inner-card action-choice-card"
                title="最新交易日一键选股"
                extra={<Tag color="blue">{latestDate || "-"}</Tag>}
              >
                <Form<SelectionFormValues>
                  layout="vertical"
                  onFinish={(values) => runSelection("latest", "selection_latest", values)}
                >
                  <Form.Item label="选股策略" name="strategies">
                    <Select allowClear mode="multiple" options={strategyOptions} placeholder="不选择则按默认策略执行" />
                  </Form.Item>
                  <Form.Item label="板块" name="boards" initialValue={BOARD_OPTIONS.map((o) => o.value)}>
                    <Select mode="multiple" options={BOARD_OPTIONS} allowClear placeholder="默认全选四板块；取消北交所可做 A/B 对比" />
                  </Form.Item>
                  <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting === "latest"}>
                    执行最新选股
                  </Button>
                </Form>
              </Card>

              <Card className="inner-card action-choice-card" title="指定日期单日选股">
                <Form<SelectionFormValues>
                  form={singleForm}
                  layout="vertical"
                  initialValues={{ date: dayjs("2026-04-01") }}
                  onFinish={(values) => runSelection("single", "selection_single", values)}
                >
                  <Form.Item label="选股日期" name="date" rules={[{ required: true, message: "请选择选股日期" }]}>
                    <DatePicker className="full-width" disabledDate={disabledNonTradingDate} format="YYYY-MM-DD" placeholder="选择交易日" />
                  </Form.Item>
                  <Form.Item label="选股策略" name="strategies">
                    <Select allowClear mode="multiple" options={strategyOptions} placeholder="不选择则按默认策略执行" />
                  </Form.Item>
                  <Form.Item label="板块" name="boards" initialValue={BOARD_OPTIONS.map((o) => o.value)}>
                    <Select mode="multiple" options={BOARD_OPTIONS} allowClear placeholder="默认全选四板块；取消北交所可做 A/B 对比" />
                  </Form.Item>
                  <Button block type="primary" htmlType="submit" icon={<CalendarOutlined />} loading={submitting === "single"}>
                    执行单日选股
                  </Button>
                </Form>
              </Card>

              <Card
                className="inner-card action-choice-card"
                title="日期区间批量选股"
                extra={<Tag color="green">{monthStartDate || "-"} ~ {latestDate || "-"}</Tag>}
              >
                <Form<SelectionFormValues>
                  form={batchForm}
                  layout="vertical"
                  onFinish={(values) => runSelection("batch", "selection_batch", values)}
                >
                  <Row gutter={12}>
                    <Col xs={24} md={12}>
                      <Form.Item label="开始日期 from" name="from" rules={[{ required: true, message: "请选择开始日期" }]}>
                        <DatePicker className="full-width" disabledDate={disabledNonTradingDate} format="YYYY-MM-DD" />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item
                        label="结束日期 to"
                        name="to"
                        dependencies={["from"]}
                        rules={[
                          { required: true, message: "请选择结束日期" },
                          ({ getFieldValue }) => ({
                            validator(_, value: Dayjs | undefined) {
                              const from = getFieldValue("from") as Dayjs | undefined;
                              if (!from || !value || !value.isBefore(from, "day")) {
                                return Promise.resolve();
                              }
                              return Promise.reject(new Error("结束日期不能早于开始日期"));
                            },
                          }),
                        ]}
                      >
                        <DatePicker className="full-width" disabledDate={disabledNonTradingDate} format="YYYY-MM-DD" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item label="选股策略" name="strategies">
                    <Select allowClear mode="multiple" options={strategyOptions} placeholder="不选择则按默认策略执行" />
                  </Form.Item>
                  <Form.Item label="板块" name="boards" initialValue={BOARD_OPTIONS.map((o) => o.value)}>
                    <Select mode="multiple" options={BOARD_OPTIONS} allowClear placeholder="默认全选四板块；取消北交所可做 A/B 对比" />
                  </Form.Item>
                  <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting === "batch"}>
                    执行批量选股
                  </Button>
                </Form>
              </Card>
            </div>
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <EditableHistoryCard<SelectionResult>
            title="选股历史"
            emptyText="暂无选股记录"
            records={runs}
            loading={runsLoading || loading}
            editing={editingHistory}
            deleting={deleting}
            selectedKeys={selectedRunIds}
            onEditingChange={setEditingHistory}
            onSelectedKeysChange={setSelectedRunIds}
            onToggleRecord={toggleRun}
            onDelete={deleteRuns}
            onRefresh={refreshRuns}
            onOpen={(executionKey) => navigate(`/selections/${encodeURIComponent(executionKey)}`)}
            renderTitle={(run) => <Text ellipsis>{run.execution_key}</Text>}
            renderDescription={(run) => (
              <Space direction="vertical" size={4}>
                <Text className="muted-text">
                  日期：{formatDateRange(run.selection_from || run.selection_date, run.selection_to || run.selection_date)}
                  {Number(run.trade_days || 1) > 1 ? ` · ${run.trade_days} 个交易日` : ""}
                </Text>
                <Text className="muted-text">{compactStrategyNames(run.strategies)}</Text>
              </Space>
            )}
            renderExtra={(run) => <Tag color="green">{totalPickCount(run)} 只</Tag>}
          />
        </Col>
      </Row>
    </div>
  );
}
