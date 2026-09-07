
import { requestJson } from "./apiClient";
import type {
  ExecutionConsoleResponse,
  ExecutionRequest,
  ExecutionSubmitResponse,
} from "../types/execution";

export interface CancelExecutionResponse {
  data: { job_id: string; cancelled: boolean };
}

export function getExecutionConsole(
  executionId: string,
  offset: number,
  signal?: AbortSignal,
): Promise<ExecutionConsoleResponse> {
  return requestJson<ExecutionConsoleResponse>(
    `/api/executions/${encodeURIComponent(executionId)}/console?offset=${offset}`,
    { signal },
  );
}

export function submitExecution(payload: ExecutionRequest): Promise<ExecutionSubmitResponse> {
  return requestJson<ExecutionSubmitResponse>("/api/executions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelExecution(executionId: string): Promise<CancelExecutionResponse> {
  return requestJson<CancelExecutionResponse>(
    `/api/executions/${encodeURIComponent(executionId)}/cancel`,
    { method: "POST" },
  );
}
