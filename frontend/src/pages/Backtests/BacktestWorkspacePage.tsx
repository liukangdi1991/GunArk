import { PlayCircleOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, DatePicker, Form, InputNumber, Row, Select, Space, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EditableHistoryCard } from "../../components/EditableHistoryCard";
import { deleteBacktestResults, listBacktestResults } from "../../services/backtests";
import { submitExecution } from "../../services/executions";
import { listTradingDates } from "../../services/marketData";
import { listSelectionResults } from "../../services/selections";
import { listStrategies } from "../../services/strategies";
import type { BacktestResult } from "../../types/backtest";
import type { SelectionResult } from "../../types/selection";
import type { Strategy } from "../../types/strategy";
import { CAPITAL_MODE_OPTIONS, capitalModeLabel } from "../../utils/capital";
import { firstTradingDateOfLatestMonth, formatPickerDate, latestTradingDate, makeDisabledNonTradingDate, toDayjs } from "../../utils/date";
import { compactStrategyNames, formatDateRange, formatPercent, parseFiniteNumber, signedClassName } from "../../utils/format";
import { TRADE_STRATEGY_OPTIONS, tradeStrategyLabel } from "../../utils/tradeStrategy";

const { Paragraph, Text, Title } = Typography;

interface BacktestFormValues {
  strategies?: string[];
  from?: Dayjs;
  to?: Dayjs;
  mode?: string;
  cash_per_trade?: number;
  trade_strategy?: string;
}

interface HistoryBacktestFormValues {
  selection_execution_keys?: string[];
  mode?: string;
  cash_per_trade?: number;
  trade_strategy?: string;
}

type SubmitKey = "history" | "selection_backtest";
export type BacktestWorkspaceMode = SubmitKey;

interface SelectionHistoryOption {
  label: string;
  value: string;
  executionKeys: string[];
  selectionFrom: string | null;
  selectionTo: string | null;
}

function selectedStrategies(values?: string[]) {
  return values && values.length ? values : null;
}

function normalizeExecutionKeys(values?: unknown): string[] {
  if (!Array.isArray(values)) {
    return [];
  }
  return values
    .map((value) => String(value || "").trim())
    .filter(Boolean);
}

function bestReturn(result: BacktestResult) {
  const values = (result.summary || [])
    .map((item) => parseFiniteNumber(item.total_return_pct))
    .filter((value): value is number => value !== null);
  return values.length ? Math.max(...values) : null;
}

function formatFinishedAt(value?: string) {
  if (!value) {
    return "完成时间未知";
  }
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("YYYY-MM-DD HH:mm:ss") : value;
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
  const [selectedHistoryKeys, setSelectedHistoryKeys] = useState<string[]>([]);
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
  const selectionHistoryOptions = useMemo(() => {
    const byGroup = new Map<string, SelectionHistoryOption>();
    for (const result of selectionResults) {
      const groupKey = result.selection_group_key || result.execution_key;
      if (byGroup.has(groupKey)) {
        continue;
      }
      const executionKeys = result.selection_execution_keys?.length
        ? result.selection_execution_keys
        : [result.execution_key];
      const selectionFrom = result.selection_from || result.selection_date || null;
      const selectionTo = result.selection_to || result.selection_date || null;
      const dateLabel = formatDateRange(selectionFrom, selectionTo);
      const tradeDays = Number(result.trade_days || executionKeys.length || 1);
      const dayCount = tradeDays > 1 ? ` · ${tradeDays} 个交易日` : "";
      byGroup.set(groupKey, {
        label: `${dateLabel} · ${compactStrategyNames(result.strategies)}${dayCount}`,
        value: groupKey,
        executionKeys,
        selectionFrom,
        selectionTo,
      });
    }
    return Array.from(byGroup.values());
  }, [selectionResults]);
  const selectionOptionMap = useMemo(
    () => new Map(selectionHistoryOptions.map((option) => [option.value, option])),
    [selectionHistoryOptions],
  );
  const selectionOptions = useMemo(
    () => selectionHistoryOptions.map((option) => ({
      label: option.label,
      value: option.value,
    })),
    [selectionHistoryOptions],
  );
  const selectedHistoryExecutionKeys = useMemo(() => {
    const keys: string[] = [];
    const seen = new Set<string>();
    for (const selectedValue of selectedHistoryKeys) {
      const option = selectionOptionMap.get(selectedValue);
      const optionKeys = option?.executionKeys.length ? option.executionKeys : [selectedValue];
      for (const executionKey of optionKeys) {
        if (!seen.has(executionKey)) {
          keys.push(executionKey);
          seen.add(executionKey);
        }
      }
    }
    return keys;
  }, [selectedHistoryKeys, selectionOptionMap]);
  const selectedHistoryKeySet = useMemo(() => new Set(selectedHistoryExecutionKeys), [selectedHistoryExecutionKeys]);
  const selectedSelectionResults = useMemo(
    () => selectionResults.filter((result) => selectedHistoryKeySet.has(result.execution_key)),
    [selectionResults, selectedHistoryKeySet],
  );
  const selectedHistoryRange = useMemo(() => {
    const optionDates = selectedHistoryKeys
      .flatMap((selectedValue) => {
        const option = selectionOptionMap.get(selectedValue);
        return option ? [option.selectionFrom, option.selectionTo] : [];
      })
      .filter(Boolean)
      .map(String);
    const dates = (optionDates.length
      ? optionDates
      : selectedSelectionResults.map((result) => result.selection_date).filter(Boolean)
    ).sort();
    if (!dates.length) {
      return "-";
    }
    return dates[0] === dates[dates.length - 1] ? dates[0] : `${dates[0]} ~ ${dates[dates.length - 1]}`;
  }, [selectedHistoryKeys, selectedSelectionResults, selectionOptionMap]);
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
      setSelectedHistoryKeys([]);
      setTradingDates(dates);
      historyForm.setFieldsValue({
        mode: "unlimited_cash",
        cash_per_trade: 50000,
        trade_strategy: "long_term_bull_bear_stop",
      });
      selectionBacktestForm.setFieldsValue({
        from: toDayjs(firstTradingDateOfLatestMonth(dates)),
        to: toDayjs(latestTradingDate(dates)),
        mode: "unlimited_cash",
        cash_per_trade: 50000,
        trade_strategy: "long_term_bull_bear_stop",
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
      const selectedValues = normalizeExecutionKeys(values.selection_execution_keys);
      const selectionExecutionKeys = Array.from(
        new Set(
          selectedValues.flatMap((value) => {
            const option = selectionOptionMap.get(value);
            return option?.executionKeys.length ? option.executionKeys : [value];
          }),
        ),
      );
      const execution = await submitExecution({
        type: "backtest_from_selection",
        params: {
          selection_execution_keys: selectionExecutionKeys,
          mode: values.mode || "unlimited_cash",
          cash_per_trade: values.cash_per_trade || 50000,
          trade_strategy: values.trade_strategy || "long_term_bull_bear_stop",
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
          trade_strategy: values.trade_strategy || "long_term_bull_bear_stop",
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
        messageApi.warning(`有 ${result.file_errors.length} 个本地文件未能删除：${result.file_errors[0]}`);
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
                  trade_strategy: "long_term_bull_bear_stop",
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
                    onChange={(values) => setSelectedHistoryKeys(normalizeExecutionKeys(values))}
                  />
                </Form.Item>
                <div className="selection-history-status">
                  <span className={selectedHistoryKeys.length ? "selection-history-pill active" : "selection-history-pill"}>
                    已选 <strong>{selectedHistoryKeys.length}</strong> 条
                  </span>
                  <span className="selection-history-pill">
                    日期范围 <strong>{selectedHistoryRange}</strong>
                  </span>
                  <span className="selection-history-pill">
                    覆盖 <strong>{selectedHistoryExecutionKeys.length}</strong> 个交易日
                  </span>
                  <span className="selection-history-note">使用所选历史里的全部股票信号回测</span>
                </div>
                <Row gutter={12}>
                  <Col xs={24} md={12}>
                    <Form.Item label="资金模式" name="mode">
                      <Select
                        options={CAPITAL_MODE_OPTIONS}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="每票金额" name="cash_per_trade">
                      <InputNumber className="full-width" min={100} precision={0} step={1000} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="交易策略" name="trade_strategy">
                  <Select options={TRADE_STRATEGY_OPTIONS} />
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
                  trade_strategy: "long_term_bull_bear_stop",
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
                        options={CAPITAL_MODE_OPTIONS}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="每票金额" name="cash_per_trade">
                      <InputNumber className="full-width" min={100} precision={0} step={1000} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="交易策略" name="trade_strategy">
                  <Select options={TRADE_STRATEGY_OPTIONS} />
                </Form.Item>
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
          <EditableHistoryCard<BacktestResult>
            title="回测历史"
            emptyText="暂无回测记录"
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
            onOpen={(executionKey) => navigate(`/backtests/${encodeURIComponent(executionKey)}`)}
            renderTitle={(run) => <Text strong>{formatFinishedAt(run.finished_at || run.created_at)}</Text>}
            renderDescription={(run) => (
              <Space direction="vertical" size={4}>
                <Text className="muted-text">回测区间：{run.start_date} ~ {run.end_date}</Text>
                {run.selection_from || run.selection_to ? (
                  <Text className="muted-text">选股日期：{formatDateRange(run.selection_from, run.selection_to)}</Text>
                ) : null}
                <Text className="muted-text">策略：{compactStrategyNames(run.strategies)}</Text>
                <Text className="muted-text">资金模式：{capitalModeLabel(run.capital_mode)}</Text>
                <Text className="muted-text">
                  交易策略：{tradeStrategyLabel(typeof run.trade_rule?.trade_strategy === "string" ? run.trade_rule.trade_strategy : undefined)}
                </Text>
              </Space>
            )}
            renderExtra={(run) => (
              <Tag color={Number(bestReturn(run) || 0) >= 0 ? "green" : "red"}>
                <span className={signedClassName(bestReturn(run))}>{formatPercent(bestReturn(run))}</span>
              </Tag>
            )}
          />
        </Col>
      </Row>
    </div>
  );
}
