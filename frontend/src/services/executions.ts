import { requestJson } from "./apiClient";
import type {
  ExecutionConsoleResponse,
  ExecutionRequest,
  ExecutionSubmitResponse,
} from "../types/execution";

export function getExecutionConsole(
  executionId: string,
  offset: number,
): Promise<ExecutionConsoleResponse> {
  return requestJson<ExecutionConsoleResponse>(
    `/api/executions/${encodeURIComponent(executionId)}/console?offset=${offset}`,
  );
}

export function submitExecution(payload: ExecutionRequest): Promise<ExecutionSubmitResponse> {
  return requestJson<ExecutionSubmitResponse>("/api/executions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
