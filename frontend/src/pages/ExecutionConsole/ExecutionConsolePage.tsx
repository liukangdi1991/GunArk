import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Progress,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  StopOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { cancelExecution, getExecutionConsole } from "../../services/executions";
import { ApiError } from "../../services/apiClient";
import type { Execution } from "../../types/execution";
import { executionPercent, logLineClassName, statusTone } from "../../utils/execution";

const { Text, Title } = Typography;

function resultMessage(execution: Execution): string {
  if (execution.status === "success") {
    return execution.result_url ? "执行完成，2 秒后自动打开结果页。" : "执行完成。";
  }
  if (execution.status === "cancelled") {
    return "任务已停止。";
  }
  return execution.error_message || "执行失败。";
}

function splitConsoleText(pendingText: string, text: string) {
  const combined = pendingText + text;
  const lines = combined.split(/\r?\n/);
  return {
    lines: lines.slice(0, -1),
    pendingText: lines[lines.length - 1] || "",
  };
}

export function ExecutionConsolePage() {
  const { executionId = "" } = useParams();
  const navigate = useNavigate();
  const [execution, setExecution] = useState<Execution | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [pendingText, setPendingText] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [cancelErrorMessage, setCancelErrorMessage] = useState("");
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const redirectedRef = useRef(false);
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const offsetRef = useRef(0);
  const pendingTextRef = useRef("");

  const percent = useMemo(() => executionPercent(execution || undefined), [execution]);
  const finished = execution?.status === "success" || execution?.status === "failed" || execution?.status === "cancelled";
  const cancellable = execution?.status === "queued" || execution?.status === "running" || execution?.status === "cancelling";

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let attempt = 0;          // 连续失败次数：决定退避档位
    let errorLogged = false;  // 一轮失败只在日志里留一行，不刷屏
    const controller = new AbortController();
    offsetRef.current = 0;
    pendingTextRef.current = "";
    setExecution(null);
    setLines([]);
    setPendingText("");
    setErrorMessage("");
    setCancelErrorMessage("");

    async function poll() {
      if (!executionId) {
        setErrorMessage("缺少 execution id。");
        return;
      }

      try {
        const payload = await getExecutionConsole(executionId, offsetRef.current, controller.signal);
        if (cancelled) {
          return;
        }

        if (errorLogged) {
          errorLogged = false;
          setLines((current) =>
            current.concat(`${new Date().toISOString()} [INFO] 日志刷新已恢复`),
          );
        }
        attempt = 0;

        setExecution(payload.execution);
        offsetRef.current = payload.offset || offsetRef.current;
        setErrorMessage("");

        if (payload.text) {
          const next = splitConsoleText(pendingTextRef.current, payload.text);
          pendingTextRef.current = next.pendingText;
          setPendingText(next.pendingText);
          setLines((current) => current.concat(next.lines));
        }

        if (payload.more) {
          timer = window.setTimeout(poll, 1000);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : "请求运行日志失败。";
        setErrorMessage(message);
        // 任务本体多半还在跑：瞬时失败（超时/网络抖动/5xx）必须退避重试，
        // 否则一次失败就把 20 分钟级任务的进度条永久冻死（只能手动刷新页面）。
        // 404 是「查无此任务」，重试无意义，如实停在这里。
        if (error instanceof ApiError && error.status === 404) {
          return;
        }
        if (!errorLogged) {
          errorLogged = true;
          setLines((current) =>
            current.concat(`${new Date().toISOString()} [ERROR] ${message}`),
          );
        }
        attempt += 1;
        const delay = Math.min(15000, 1000 * 2 ** (attempt - 1)); // 1s→2s→4s…上限 15s
        timer = window.setTimeout(poll, delay);
      }
    }

    poll();

    return () => {
      cancelled = true;
      if (timer) {
        window.clearTimeout(timer);
      }
      controller.abort(); // 中止在途轮询请求，卸载后不再占用连接
    };
  }, [executionId]);

  useEffect(() => {
    if (autoScroll && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [autoScroll, lines]);

  useEffect(() => {
    if (!execution || execution.status !== "success" || !execution.result_url || redirectedRef.current) {
      return;
    }

    redirectedRef.current = true;
    const timer = window.setTimeout(() => {
      navigate(execution.result_url || "/"); // SPA 内跳转：控制台已在主框架内（P5）
    }, 2000);

    return () => window.clearTimeout(timer);
  }, [execution, navigate]);

  const renderedLines = pendingText ? lines.concat(pendingText) : lines;

  async function handleCancel() {
    if (!executionId || !cancellable) {
      return;
    }
    setCancelSubmitting(true);
    setCancelErrorMessage("");
    try {
      // 后端返回 {data:{job_id,cancelled}} 确认——执行状态由轮询更新为
      // cancelling/cancelled，不要用它覆盖 execution 对象。
      await cancelExecution(executionId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "停止任务失败。";
      setCancelErrorMessage(message);
    } finally {
      setCancelSubmitting(false);
    }
  }

  return (
    <main className="console-shell">
      <section className="console-head">
        <div>
          <div className="eyebrow">执行控制台</div>
          <Title level={1}>运行详情</Title>
          <Text className="execution-id">{executionId || "loading..."}</Text>
        </div>
        <div className="status-card">
          <Tag color={statusTone(execution?.status)} icon={execution?.status === "running" || execution?.status === "cancelling" ? <SyncOutlined spin /> : undefined}>
            {execution?.status || "queued"}
          </Tag>
          <strong className="progress-percent">{percent}%</strong>
        </div>
      </section>

      <Card className="progress-panel">
        <Progress
          percent={percent}
          status={execution?.status === "failed" ? "exception" : undefined}
          strokeColor={{
            "0%": "#22c55e",
            "55%": "#38bdf8",
            "100%": "#f59e0b",
          }}
        />
        <Text className="progress-message">
          {(execution?.progress_current || 0)}/{execution?.progress_total || 0} ·{" "}
          {execution?.progress_message || execution?.status || "等待执行"}
        </Text>
      </Card>

      {errorMessage && (
        <Alert
          className="console-alert"
          type="error"
          showIcon
          message="日志刷新失败"
          description={errorMessage}
        />
      )}

      {cancelErrorMessage && (
        <Alert
          className="console-alert"
          type="error"
          showIcon
          message="停止任务失败"
          description={cancelErrorMessage}
        />
      )}

      <section className="console-panel">
        <div className="console-toolbar">
          <Space>
            <SyncOutlined />
            <strong>运行日志</strong>
          </Space>
          <Space>
            {cancellable ? (
              <Button
                danger
                icon={<StopOutlined />}
                loading={cancelSubmitting || execution?.status === "cancelling"}
                onClick={handleCancel}
              >
                {execution?.status === "cancelling" ? "正在停止" : "停止任务"}
              </Button>
            ) : null}
            <Button onClick={() => setAutoScroll((value) => !value)}>
              自动滚动: {autoScroll ? "开" : "关"}
            </Button>
          </Space>
        </div>
        <div ref={consoleRef} className="console-output">
          {renderedLines.map((line, index) => (
            <div className={logLineClassName(line)} key={`${index}-${line.slice(0, 20)}`}>
              {line}
            </div>
          ))}
        </div>
      </section>

      {finished && execution && (
        <Card className="result-panel">
          <Space>
            {execution.status === "success" ? (
              <CheckCircleOutlined className="result-success" />
            ) : execution.status === "cancelled" ? (
              <StopOutlined className="result-failed" />
            ) : (
              <CloseCircleOutlined className="result-failed" />
            )}
            <Text>{resultMessage(execution)}</Text>
          </Space>
          <Button type="primary" href={execution.result_url || "/"}>
            {execution.result_url ? "立即查看结果" : "返回工作台"}
          </Button>
        </Card>
      )}
    </main>
  );
}
