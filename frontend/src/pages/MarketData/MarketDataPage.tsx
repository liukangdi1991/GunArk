import { DatabaseOutlined, PlayCircleOutlined, ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  Alert, Button, Card, Checkbox, Col, Collapse, Modal, Row, Select, Space,
  Statistic, Tag, Typography, message,
} from "antd";
import { useEffect, useState } from "react";
import { submitExecution } from "../../services/executions";
import { ApiError } from "../../services/apiClient";
import {
  confirmDoubtfulDays, getMarketDataStatus, submitMarketBackfill,
} from "../../services/marketData";
import type { MarketDataStatus } from "../../types/marketData";

const { Paragraph, Text, Title } = Typography;

const STATUS_TEXT: Record<string, string> = {
  success: "成功",
  failed: "失败",
  running: "运行中",
  queued: "排队中",
  cancelled: "已取消",
  cancelling: "取消中",
};

export function MarketDataPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const [modal, modalContextHolder] = Modal.useModal();
  const [status, setStatus] = useState<MarketDataStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [excludeBoards, setExcludeBoards] = useState<string[]>([]);

  async function refreshStatus() {
    setLoading(true);
    try {
      const payload = await getMarketDataStatus();
      setStatus(payload);
      setErrorMessage("");
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载行情状态失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshStatus();
  }, []);

  async function runSync(force: boolean, acceptPartialBaseline: boolean) {
    setSubmitting(true);
    try {
      const execution = await submitExecution({
        type: "market_bars_sync",
        params: {
          force,
          exclude_boards: excludeBoards,
          accept_partial_baseline: acceptPartialBaseline,
        },
      });
      window.location.href = execution.console_url;
    } catch (error) {
      // 409 = 互斥拒绝（已有同步在跑），属良性冲突，不当错误染红
      if (error instanceof ApiError && error.status === 409) {
        messageApi.warning("同步进行中，稍后再试");
      } else {
        const message = error instanceof Error && error.message
          ? error.message : "提交行情同步任务失败。";
        setErrorMessage(message);
        messageApi.error(message);
      }
    } finally {
      setSubmitting(false);
    }
  }

  function confirmFullRebuild() {
    let acceptPartial = false;
    const missing = status?.coverage.missing_codes ?? 0;
    modal.confirm({
      title: "全量重建",
      content: (
        <div>
          <Paragraph>约 41 分钟 / 约 11,100 次调用 / 将覆盖现有数据。</Paragraph>
          {missing > 0 ? (
            <Checkbox onChange={(e) => { acceptPartial = e.target.checked; }}>
              存在 {missing} 只个股缺口，勾选后以带缺口方式入账
            </Checkbox>
          ) : null}
        </div>
      ),
      okText: "开始重建",
      cancelText: "取消",
      onOk: () => runSync(true, acceptPartial),
    });
  }

  async function runBackfill() {
    setBackfilling(true);
    try {
      const resp = await submitMarketBackfill();
      window.location.href = `/console/${resp.data.job_id}`;
    } catch (error) {
      // 409 = 互斥拒绝（主同步运行中）
      if (error instanceof ApiError && error.status === 409) {
        messageApi.warning("同步进行中，稍后再试");
      } else {
        messageApi.warning(error instanceof Error && error.message
          ? error.message : "提交补齐任务失败。");
      }
    } finally {
      setBackfilling(false);
    }
  }

  async function runConfirmDoubtful() {
    setConfirming(true);
    try {
      const resp = await confirmDoubtfulDays();
      messageApi.success(`已确认入账 ${resp.confirmed.length} 个交易日`);
      await refreshStatus();
    } catch (error) {
      const text = error instanceof Error ? error.message : "确认入账失败。";
      messageApi.error(text);
    } finally {
      setConfirming(false);
    }
  }

  const freshness = status?.freshness;
  const consistency = status?.consistency;
  const showWarningRow =
    !!consistency &&
    (consistency.ledger_suspect || consistency.marker_mismatch || consistency.doubtful_days.length > 0);

  return (
    <div className="feature-page">
      {contextHolder}
      {modalContextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Market Data</div>
          <Title level={1}>行情数据</Title>
          <Paragraph className="muted-text">查看本地行情状态，或提交日线数据同步任务。</Paragraph>
        </div>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Row gutter={[12, 12]} className="workspace-metrics">
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="股票数" value={status?.storage.stock_count ?? 0} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="本地文件" value={status?.storage.local_file_count ?? 0} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="最新行情日期" value={status?.storage.latest_date ?? "-"} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <div className="metric-inline">
                  <Text className="metric-label">数据状态</Text>
                  <span className="metric-value">
                    {status?.storage.latest_date ? "已初始化" : "待初始化"}
                  </span>
                </div>
              </Card>
            </Col>
          </Row>

          <Card className="workbench-card" title="同步行情" style={{ marginTop: 12 }}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Button
                block type="primary" icon={<PlayCircleOutlined />}
                loading={submitting} onClick={() => runSync(false, false)}
              >
                补齐到最近可交易日
              </Button>
              <Collapse
                ghost
                items={[{
                  key: "advanced",
                  label: "高级",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Select
                        style={{ width: "100%" }}
                        allowClear mode="multiple"
                        value={excludeBoards}
                        onChange={setExcludeBoards}
                        options={[
                          { label: "创业板 gem", value: "gem" },
                          { label: "科创板 star", value: "star" },
                        ]}
                        placeholder="排除板块（默认不排除；北交所永久剔除）"
                      />
                      <Button
                        block danger icon={<ThunderboltOutlined />}
                        loading={submitting} onClick={confirmFullRebuild}
                      >
                        全量重建
                      </Button>
                      <Text className="muted-text">
                        约 41 分钟 / 约 11,100 次调用 / 将覆盖现有数据
                      </Text>
                    </Space>
                  ),
                }]}
              />
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            className="workbench-card"
            loading={loading}
            title={
              <Space>
                <DatabaseOutlined />
                <span>数据地基</span>
              </Space>
            }
            extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refreshStatus}>刷新</Button>}
          >
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              {/* ① 日历 */}
              <div>
                <Text strong>日历</Text>{" "}
                {status?.calendar.max_trade_date ? (
                  <>
                    <Text className="muted-text">覆盖至 {status.calendar.max_trade_date}</Text>{" "}
                    {status.calendar.status === "failed" ? (
                      <Tag color="red">
                        拉取失败{" "}
                        {status.calendar.job_id ? (
                          <a href={`/console/${status.calendar.job_id}`}>查看控制台</a>
                        ) : null}
                      </Tag>
                    ) : (
                      <Tag color={status.calendar.covers_today ? "green" : "orange"}>
                        {status.calendar.covers_today ? "正常" : "待刷新"}
                      </Tag>
                    )}
                  </>
                ) : (
                  <Text type="secondary">日历未就绪，行情同步已阻断</Text>
                )}
              </div>

              {/* ② 数据新鲜度 */}
              <div>
                <Text strong>数据新鲜度</Text>{" "}
                {freshness?.stale_days == null ? (
                  <Text type="secondary">未建库</Text>
                ) : freshness.stale_days === 0 ? (
                  <Tag color="green">已最新</Tag>
                ) : (
                  <Tag color="red">落后 {freshness.stale_days} 个交易日</Tag>
                )}
                {freshness?.latest_tradeable ? (
                  <div>
                    <Text className="muted-text" type="secondary">
                      最近可交易日 {freshness.latest_tradeable} · 可信边界{" "}
                      {freshness.trusted_through ?? "-"}
                      {freshness.total_missing_days != null &&
                        freshness.total_missing_days > (freshness.stale_days ?? 0) &&
                        ` · 总缺口 ${freshness.total_missing_days} 天`}
                    </Text>
                  </div>
                ) : null}
              </div>

              {/* ③ 最近一次行情同步 */}
              <div>
                <Text strong>最近一次行情同步</Text>{" "}
                {status?.bars_sync.status ? (
                  <>
                    <Text className="muted-text">{status.bars_sync.finished_at ?? "进行中"}</Text>{" "}
                    <Tag color={status.bars_sync.status === "success" ? "green" : "red"}>
                      {STATUS_TEXT[status.bars_sync.status] ?? status.bars_sync.status}
                    </Tag>
                    {status.bars_sync.job_id ? (
                      <a href={`/console/${status.bars_sync.job_id}`}>查看控制台</a>
                    ) : null}
                  </>
                ) : (
                  <Text type="secondary">暂无同步记录</Text>
                )}
              </div>

              {/* ④ 个股覆盖 */}
              <div>
                <Text strong>个股覆盖</Text>{" "}
                {(status?.coverage.missing_codes ?? 0) > 0 ? (
                  <>
                    <Tag color="red">缺 {status!.coverage.missing_codes} 只（连续失败）</Tag>
                    <Button
                      size="small" loading={backfilling} onClick={runBackfill}
                    >
                      立即补齐
                    </Button>
                  </>
                ) : (
                  <Text type="secondary">无缺口</Text>
                )}
              </div>

              {/* ⑤ 账本一致性（条件出现） */}
              {showWarningRow ? (
                <div>
                  <Text strong>⚠ 账本一致性</Text>
                  <div>
                    {consistency!.ledger_suspect ? (
                      <Text type="danger">
                        账本曾被判不可信，需手动全量重建
                        <Button size="small" danger style={{ marginLeft: 8 }} onClick={confirmFullRebuild}>
                          全量重建
                        </Button>
                      </Text>
                    ) : null}
                    {consistency!.marker_mismatch ? (
                      <div><Text type="danger">账本与文件不一致（读回对账失败）</Text></div>
                    ) : null}
                    {consistency!.doubtful_days.length > 0 ? (
                      <div>
                        <Text type="danger">
                          {consistency!.doubtful_days.length} 个交易日行数异常已跳过
                        </Text>
                        <Button
                          size="small" style={{ marginLeft: 8 }} loading={confirming}
                          onClick={runConfirmDoubtful}
                        >
                          确认入账
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* ⑥ 基线 */}
              <div>
                <Text strong>基线</Text>{" "}
                <Text className="muted-text">
                  2015-01-01 起 · {status?.storage.stock_count ?? 0} 只 · 最新{" "}
                  {status?.storage.latest_date ?? "-"}
                </Text>
              </div>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
