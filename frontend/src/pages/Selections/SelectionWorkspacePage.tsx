import { CalendarOutlined, DeleteOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Col, DatePicker, Form, List, Popconfirm, Row, Select, Space, Statistic, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { submitExecution } from "../../services/executions";
import { listTradingDates } from "../../services/marketData";
import { deleteSelectionResults, listSelectionResults } from "../../services/selections";
import { listStrategies } from "../../services/strategies";
import type { ExecutionRequestType } from "../../types/execution";
import type { SelectionResult } from "../../types/selection";
import type { Strategy } from "../../types/strategy";
import { makeDisabledNonTradingDate, formatPickerDate, firstTradingDateOfLatestMonth, latestTradingDate, toDayjs } from "../../utils/date";
import { compactStrategyNames } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

type SubmitKey = "latest" | "single" | "batch";

interface SelectionFormValues {
  strategies?: string[];
  date?: Dayjs;
  from?: Dayjs;
  to?: Dayjs;
}

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
  const selectedRunSet = useMemo(() => new Set(selectedRunIds), [selectedRunIds]);
  const allRunIds = useMemo(() => runs.map((run) => run.execution_key), [runs]);
  const allSelected = runs.length > 0 && selectedRunIds.length === runs.length;
  const partiallySelected = selectedRunIds.length > 0 && selectedRunIds.length < runs.length;

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
        messageApi.warning(`有 ${result.file_errors.length} 个本地文件未能删除，请检查日志或手动清理。`);
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
                  <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting === "batch"}>
                    执行批量选股
                  </Button>
                </Form>
              </Card>
            </div>
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            className="workbench-card"
            title="选股历史"
            extra={
              <Space size={8} wrap>
                {editingHistory ? (
                  <>
                    <Checkbox
                      checked={allSelected}
                      indeterminate={partiallySelected}
                      onChange={(event) => setSelectedRunIds(event.target.checked ? allRunIds : [])}
                    >
                      全选
                    </Checkbox>
                    <Popconfirm
                      title="删除选中的选股历史？"
                      description={`将删除 ${selectedRunIds.length} 条历史记录及对应本地结果文件。`}
                      okText="删除"
                      cancelText="取消"
                      disabled={!selectedRunIds.length}
                      onConfirm={() => deleteRuns(selectedRunIds)}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} loading={deleting} disabled={!selectedRunIds.length}>
                        删除选中
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title="清空全部选股历史？"
                      description="将删除所有选股历史记录及对应本地结果文件。"
                      okText="清空"
                      cancelText="取消"
                      disabled={!runs.length}
                      onConfirm={() => deleteRuns()}
                    >
                      <Button size="small" danger loading={deleting} disabled={!runs.length}>
                        清空
                      </Button>
                    </Popconfirm>
                    <Button
                      size="small"
                      onClick={() => {
                        setEditingHistory(false);
                        setSelectedRunIds([]);
                      }}
                    >
                      取消
                    </Button>
                  </>
                ) : (
                  <Button size="small" onClick={() => setEditingHistory(true)}>
                    编辑
                  </Button>
                )}
                <Button size="small" icon={<ReloadOutlined />} loading={runsLoading} onClick={refreshRuns}>刷新</Button>
              </Space>
            }
          >
            <List
              className="selection-run-list"
              loading={runsLoading || loading}
              dataSource={runs}
              locale={{ emptyText: "暂无选股记录" }}
              renderItem={(run) => (
                <List.Item
                  onClick={() => {
                    if (editingHistory) {
                      toggleRun(run.execution_key, !selectedRunSet.has(run.execution_key));
                      return;
                    }
                    navigate(`/selections/${encodeURIComponent(run.execution_key)}`);
                  }}
                >
                  {editingHistory ? (
                    <Checkbox
                      checked={selectedRunSet.has(run.execution_key)}
                      onClick={(event) => event.stopPropagation()}
                      onChange={(event) => toggleRun(run.execution_key, event.target.checked)}
                    />
                  ) : null}
                  <List.Item.Meta
                    title={<Text ellipsis>{run.execution_key}</Text>}
                    description={
                      <Space direction="vertical" size={4}>
                        <Text className="muted-text">{run.selection_date}</Text>
                        <Text className="muted-text">{compactStrategyNames(run.strategies)}</Text>
                      </Space>
                    }
                  />
                  <Tag color="green">{totalPickCount(run)} 只</Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
