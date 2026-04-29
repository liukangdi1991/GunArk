import { DatabaseOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, DatePicker, Form, Row, Select, Space, Statistic, Typography, message } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";
import { submitExecution } from "../../services/executions";
import { getMarketDataStatus } from "../../services/marketData";
import type { MarketDataStatus } from "../../types/marketData";
import { formatPickerDate } from "../../utils/date";

const { Paragraph, Text, Title } = Typography;

interface MarketFetchFormValues {
  start?: Dayjs;
  end?: Dayjs;
  exclude_boards?: string[];
}

export function MarketDataPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const [form] = Form.useForm<MarketFetchFormValues>();
  const [status, setStatus] = useState<MarketDataStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

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

  async function runMarketFetch(values: MarketFetchFormValues) {
    setSubmitting(true);
    try {
      const execution = await submitExecution({
        type: "market_data_sync",
        params: {
          start: formatPickerDate(values.start),
          end: formatPickerDate(values.end),
          exclude_boards: values.exclude_boards || [],
        },
      });
      window.location.href = execution.console_url;
    } catch (error) {
      const text = error instanceof Error ? error.message : "提交行情拉取任务失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    form.setFieldsValue({
      start: dayjs("2019-01-01"),
      end: dayjs(),
      exclude_boards: [],
    });
    void refreshStatus();
  }, []);

  return (
    <div className="feature-page">
      {contextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Market Data</div>
          <Title level={1}>行情数据</Title>
          <Paragraph className="muted-text">查看本地行情状态，或提交日线数据拉取任务。</Paragraph>
        </div>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Row gutter={[12, 12]} className="workspace-metrics">
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="股票数" value={status?.stock_count || 0} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="本地文件" value={status?.local_file_count || 0} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <Statistic title="最新行情日期" value={status?.latest_date || "-"} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card className="metric-card" loading={loading}>
            <div className="metric-inline">
              <Text className="metric-label">数据状态</Text>
              <span className="metric-value">{status?.latest_date ? "已初始化" : "待初始化"}</span>
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card
            className="workbench-card"
            title="拉取行情"
          >
            <Form<MarketFetchFormValues>
              form={form}
              layout="vertical"
              initialValues={{
                start: dayjs("2019-01-01"),
                end: dayjs(),
                exclude_boards: [],
              }}
              onFinish={runMarketFetch}
            >
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Form.Item label="起始日期" name="start" rules={[{ required: true, message: "请选择起始日期" }]}>
                    <DatePicker className="full-width" format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item
                    label="结束日期"
                    name="end"
                    dependencies={["start"]}
                    rules={[
                      { required: true, message: "请选择结束日期" },
                      ({ getFieldValue }) => ({
                        validator(_, value: Dayjs | undefined) {
                          const start = getFieldValue("start") as Dayjs | undefined;
                          if (!start || !value || !value.isBefore(start, "day")) {
                            return Promise.resolve();
                          }
                          return Promise.reject(new Error("结束日期不能早于起始日期"));
                        },
                      }),
                    ]}
                  >
                    <DatePicker className="full-width" format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="排除板块" name="exclude_boards">
                <Select
                  allowClear
                  mode="multiple"
                  options={[
                    { label: "创业板 gem", value: "gem" },
                    { label: "科创板 star", value: "star" },
                    { label: "北交所 bj", value: "bj" },
                  ]}
                  placeholder="默认不排除"
                />
              </Form.Item>
              <Button block type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting}>
                拉取行情数据
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            className="workbench-card"
            loading={loading}
            title={
              <Space>
                <DatabaseOutlined />
                <span>本地存储</span>
              </Space>
            }
            extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refreshStatus}>刷新</Button>}
          >
            <div className="storage-summary">
              <div>
                <Text className="metric-label">最新行情</Text>
                <Text className="metric-value">{status?.latest_date || "-"}</Text>
              </div>
              <div>
                <Text className="metric-label">覆盖股票</Text>
                <Text className="metric-value">{status?.stock_count || 0} 只</Text>
              </div>
            </div>
            <div className="data-paths">
              <Text className="muted-text">数据目录: {status?.data_dir || "-"}</Text>
              <Text className="muted-text">股票列表: {status?.stocklist || "-"}</Text>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
