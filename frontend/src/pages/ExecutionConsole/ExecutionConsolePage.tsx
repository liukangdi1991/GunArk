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
  SyncOutlined,
} from "@ant-design/icons";
import { useParams } from "react-router-dom";
import { getExecutionConsole } from "../../services/executions";
import type { Execution } from "../../types/execution";
import { executionPercent, logLineClassName, statusTone } from "../../utils/execution";

const { Text, Title } = Typography;

function resultMessage(execution: Execution): string {
  if (execution.status === "success") {
    return execution.result_url ? "执行完成，2 秒后自动打开结果页。" : "执行完成。";
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
  const [execution, setExecution] = useState<Execution | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [pendingText, setPendingText] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [redirected, setRedirected] = useState(false);
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const offsetRef = useRef(0);
  const pendingTextRef = useRef("");

  const percent = useMemo(() => executionPercent(execution || undefined), [execution]);
  const finished = execution?.status === "success" || execution?.status === "failed";

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    offsetRef.current = 0;
    pendingTextRef.current = "";
    setExecution(null);
    setLines([]);
    setPendingText("");
    setErrorMessage("");
    setRedirected(false);

    async function poll() {
      if (!executionId) {
        setErrorMessage("缺少 execution id。");
        return;
      }

      try {
        const payload = await getExecutionConsole(executionId, offsetRef.current);
        if (cancelled) {
          return;
        }

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
        setLines((current) =>
          current.concat(`${new Date().toISOString()} [ERROR] ${message}`),
        );
      }
    }

    poll();

    return () => {
      cancelled = true;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [executionId]);

  useEffect(() => {
    if (autoScroll && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [autoScroll, lines]);

  useEffect(() => {
    if (!execution || execution.status !== "success" || !execution.result_url || redirected) {
      return;
    }

    setRedirected(true);
    const timer = window.setTimeout(() => {
      window.location.href = execution.result_url || "/";
    }, 2000);

    return () => window.clearTimeout(timer);
  }, [execution, redirected]);

  const renderedLines = pendingText ? lines.concat(pendingText) : lines;

  return (
    <main className="console-shell">
      <section className="console-head">
        <div>
          <div className="eyebrow">执行控制台</div>
          <Title level={1}>运行详情</Title>
          <Text className="execution-id">{executionId || "loading..."}</Text>
        </div>
        <div className="status-card">
          <Tag color={statusTone(execution?.status)} icon={execution?.status === "running" ? <SyncOutlined spin /> : undefined}>
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

      <section className="console-panel">
        <div className="console-toolbar">
          <Space>
            <SyncOutlined />
            <strong>运行日志</strong>
          </Space>
          <Button onClick={() => setAutoScroll((value) => !value)}>
            自动滚动: {autoScroll ? "开" : "关"}
          </Button>
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
