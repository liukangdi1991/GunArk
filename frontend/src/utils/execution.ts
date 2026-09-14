import type { Execution, ExecutionStatus } from "../types/execution";

/** 只依赖进度字段：控制台页的 Execution 与侧边栏的 ActiveExecution 共用一套百分比口径。 */
export function executionPercent(
  execution?: Pick<Execution, "progress_current" | "progress_total" | "status">,
): number {
  if (!execution) {
    return 0;
  }
  const total = Number(execution.progress_total || 0);
  const current = Number(execution.progress_current || 0);
  if (total <= 0) {
    return execution.status === "success" || execution.status === "cancelled" ? 100 : 0;
  }
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
}

export function statusTone(status?: ExecutionStatus): "processing" | "success" | "error" | "warning" {
  if (status === "success") {
    return "success";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "cancelled") {
    return "warning";
  }
  if (status === "running" || status === "cancelling") {
    return "processing";
  }
  return "warning";
}

export function logLineClassName(line: string): string {
  const match = line.match(/\[(INFO|WARN|ERROR|FAILED|CANCELLED)\]/i);
  if (!match) {
    return "log-line log-info";
  }
  return `log-line log-${match[1].toLowerCase()}`;
}
