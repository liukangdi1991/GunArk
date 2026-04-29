import { DeleteOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Col, DatePicker, Form, InputNumber, List, Popconfirm, Row, Select, Space, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteBacktestResults, listBacktestResults } from "../../services/backtests";
import { submitExecution } from "../../services/executions";
import { listTradingDates } from "../../services/marketData";
import { listSelectionResults } from "../../services/selections";
import { listStrategies } from "../../services/strategies";
import type { BacktestResult } from "../../types/backtest";
import type { SelectionResult } from "../../types/selection";
import type { Strategy } from "../../types/strategy";
import { firstTradingDateOfLatestMonth, formatPickerDate, latestTradingDate, makeDisabledNonTradingDate, toDayjs } from "../../utils/date";
import { compactStrategyNames, formatPercent, signedClassName } from "../../utils/format";

const { Paragraph, Text, Title } = Typography;

interface BacktestFormValues {
  strategies?: string[];
  from?: Dayjs;
  to?: Dayjs;
  mode?: string;
  cash_per_trade?: number;
}

interface HistoryBacktestFormValues {
  selection_execution_keys?: string[];
  strategies?: string[];
  mode?: string;
  cash_per_trade?: number;
}

type SubmitKey = "history" | "selection_backtest";
export type BacktestWorkspaceMode = SubmitKey;

function selectedStrategies(values?: string[]) {
  return values && values.length ? values : null;
}

function bestReturn(result: BacktestResult) {
  return result.summary?.[0]?.total_return_pct;
}

interface BacktestWorkspacePageProps {
  mode: BacktestWorkspaceMode;
}

export function BacktestWorkspacePage({ mode }: BacktestWorkspacePageProps) {
  const [messageApi, contextHolder] = message.useMessage();
  const [historyForm] = Form.useForm<HistoryBacktestFormValues>();
  const [selectionBacktestForm] = Form.useForm<BacktestFormValues>();
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [runs, setRuns] = useState<BacktestResult[]>([]);
  const [selectionResults, setSelectionResults] = useState<SelectionResult[]>([]);
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
  const selectionOptions = useMemo(
    () => selectionResults.map((result) => ({
      label: `${result.selection_date} · ${compactStrategyNames(result.strategies)} · ${result.execution_key}`,
      value: result.execution_key,
    })),
    [selectionResults],
  );
  const selectedHistoryKeys = (Form.useWatch("selection_execution_keys", historyForm) || []) as string[];
  const selectedSelectionResults = useMemo(
    () => selectionResults.filter((result) => selectedHistoryKeys.includes(result.execution_key)),
    [selectionResults, selectedHistoryKeys],
  );
  const selectedHistoryRange = useMemo(() => {
    const dates = selectedSelectionResults
      .map((result) => result.selection_date)
      .filter(Boolean)
      .sort();
    if (!dates.length) {
      return "-";
    }
    return dates[0] === dates[dates.length - 1] ? dates[0] : `${dates[0]} ~ ${dates[dates.length - 1]}`;
  }, [selectedSelectionResults]);
  const selectedRunSet = useMemo(() => new Set(selectedRunIds), [selectedRunIds]);
  const allRunIds = useMemo(() => runs.map((run) => run.execution_key), [runs]);
  const allSelected = runs.length > 0 && selectedRunIds.length === runs.length;
  const partiallySelected = selectedRunIds.length > 0 && selectedRunIds.length < runs.length;
  const pageCopy = mode === "history"
    ? {
      title: "根据选股历史回测",
      description: "选择已有选股结果，直接使用对应信号文件执行回测。",
      cardTitle: "根据选股历史回测",
    }
    : {
      title: "选股回测",
      description: "先按日期区间执行选股，再使用新生成的选股结果执行回测。",
      cardTitle: "选股回测",
    };

  async function refreshRuns() {
    setRunsLoading(true);
    try {
      const payload = await listBacktestResults();
      setRuns(payload.results || []);
      setSelectedRunIds((current) => current.filter((runId) => (payload.results || []).some((run) => run.execution_key === runId)));
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载回测历史失败。";
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
        listBacktestResults(),
        listTradingDates(),
      ]);
      const selectionPayload = await listSelectionResults(200);
      const dates = tradingDatePayload.dates || [];
      setStrategies(strategyPayload.strategies || []);
      setRuns(runsPayload.results || []);
      setSelectionResults(selectionPayload.results || []);
      setSelectedRunIds([]);
      setTradingDates(dates);
      historyForm.setFieldsValue({
        mode: "unlimited_cash",
        cash_per_trade: 50000,
      });
      selectionBacktestForm.setFieldsValue({
        from: toDayjs(firstTradingDateOfLatestMonth(dates)),
        to: toDayjs(latestTradingDate(dates)),
        mode: "unlimited_cash",
        cash_per_trade: 50000,
      });
      setErrorMessage("");
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载回测功能失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setLoading(false);
    }
  }

  async function runHistoryBacktest(values: HistoryBacktestFormValues) {
    setSubmitting("history");
    try {
      const execution = await submitExecution({
        type: "backtest_from_selection",
        params: {
          selection_execution_keys: values.selection_execution_keys || [],
          strategies: selectedStrategies(values.strategies),
          mode: values.mode || "unlimited_cash",
          cash_per_trade: values.cash_per_trade || 50000,
        },
      });
      window.location.href = execution.console_url;
    } catch (error) {
      const text = error instanceof Error ? error.message : "提交历史选股回测任务失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setSubmitting(null);
    }
  }

  async function runSelectionBacktest(values: BacktestFormValues) {
    setSubmitting("selection_backtest");
    try {
      const execution = await submitExecution({
        type: "selection_backtest",
        params: {
          from: formatPickerDate(values.from),
          to: formatPickerDate(values.to),
          strategies: selectedStrategies(values.strategies),
          mode: values.mode || "unlimited_cash",
          cash_per_trade: values.cash_per_trade || 50000,
        },
      });
      window.location.href = execution.console_url;
    } catch (error) {
      const text = error instanceof Error ? error.message : "提交回测任务失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setSubmitting(null);
    }
  }

  async function deleteRuns(runIds?: string[]) {
    setDeleting(true);
    try {
      const result = await deleteBacktestResults(runIds);
      messageApi.success(`已删除 ${result.deleted} 条回测历史`);
      if (result.file_errors?.length) {
        messageApi.warning(`有 ${result.file_errors.length} 个本地文件未能删除，请检查日志或手动清理。`);
      }
      setSelectedRunIds([]);
      if (!runIds) {
        setEditingHistory(false);
      }
      await refreshRuns();
    } catch (error) {
      const text = error instanceof Error ? error.message : "删除回测历史失败。";
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

  useEffect(() => {
    void loadPage();
  }, []);

  return (
    <div className="feature-page">
      {contextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Backtest</div>
          <Title level={1}>{pageCopy.title}</Title>
          <Paragraph className="muted-text">{pageCopy.description}报告在独立页面查看。</Paragraph>
        </div>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card className="workbench-card" loading={loading} title={pageCopy.cardTitle}>
            {mode === "history" ? (
              <Form<HistoryBacktestFormValues>
                form={historyForm}
                layout="vertical"
                initialValues={{
                  mode: "unlimited_cash",
                  cash_per_trade: 50000,
                }}
                onFinish={runHistoryBacktest}
              >
                <Form.Item
                  label="选股历史"
                  name="selection_execution_keys"
                  rules={[{ required: true, message: "请选择至少一条选股历史" }]}
                >
                  <Select
                    allowClear
                    mode="multiple"
                    maxTagCount="responsive"
                    options={selectionOptions}
                    placeholder="选择一个或多个选股结果"
                  />
                </Form.Item>
                <Alert
                  className="workbench-alert"
                  type="info"
                  showIcon
                  message={`已选 ${selectedSelectionResults.length} 条选股历史，信号日期范围: ${selectedHistoryRange}`}
                />
                <Row gutter={12}>
                  <Col xs={24} md={12}>
                    <Form.Item label="资金模式" name="mode">
                      <Select
                        options={[
                          { label: "unlimited_cash", value: "unlimited_cash" },
                          { label: "realistic", value: "realistic" },
                        ]}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="每票金额" name="cash_per_trade">
                      <InputNumber className="full-width" min={100} precision={0} step={1000} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="回测策略" name="strategies">
                  <Select allowClear mode="multiple" options={strategyOptions} placeholder="不选择则回测选股结果里的全部策略" />
                </Form.Item>
                <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting === "history"}>
                  根据选股历史回测
                </Button>
              </Form>
            ) : (
              <Form<BacktestFormValues>
                form={selectionBacktestForm}
                layout="vertical"
                initialValues={{
                  from: dayjs("2026-04-01"),
                  to: dayjs("2026-04-10"),
                  mode: "unlimited_cash",
                  cash_per_trade: 50000,
                }}
                onFinish={runSelectionBacktest}
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
                <Row gutter={12}>
                  <Col xs={24} md={12}>
                    <Form.Item label="资金模式" name="mode">
                      <Select
                        options={[
                          { label: "unlimited_cash", value: "unlimited_cash" },
                          { label: "realistic", value: "realistic" },
                        ]}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="每票金额" name="cash_per_trade">
                      <InputNumber className="full-width" min={100} precision={0} step={1000} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="选股策略" name="strategies">
                  <Select allowClear mode="multiple" options={strategyOptions} placeholder="不选择则按默认策略选股并回测" />
                </Form.Item>
                <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting === "selection_backtest"}>
                  先选股再回测
                </Button>
              </Form>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            className="workbench-card"
            title="回测历史"
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
                      title="删除选中的回测历史？"
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
                      title="清空全部回测历史？"
                      description="将删除所有回测历史记录及对应本地结果文件。"
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
              locale={{ emptyText: "暂无回测记录" }}
              renderItem={(run) => (
                <List.Item
                  onClick={() => {
                    if (editingHistory) {
                      toggleRun(run.execution_key, !selectedRunSet.has(run.execution_key));
                      return;
                    }
                    navigate(`/backtests/${encodeURIComponent(run.execution_key)}`);
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
                        <Text className="muted-text">{run.start_date} ~ {run.end_date}</Text>
                        <Text className="muted-text">{compactStrategyNames(run.strategies)}</Text>
                      </Space>
                    }
                  />
                  <Tag color={Number(bestReturn(run) || 0) >= 0 ? "green" : "red"}>
                    <span className={signedClassName(bestReturn(run))}>{formatPercent(bestReturn(run))}</span>
                  </Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
