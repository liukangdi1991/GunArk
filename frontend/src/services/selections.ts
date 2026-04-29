import { requestJson } from "./apiClient";
import type {
  SelectionResultDetailResponse,
  SelectionResultsResponse,
} from "../types/selection";

export interface DeleteResultsResponse {
  result_type: string;
  requested: number;
  deleted: number;
  missing: string[];
  file_errors: Array<{ storage_key: string; error: string }>;
}

export function listSelectionResults(limit = 50): Promise<SelectionResultsResponse> {
  return requestJson<SelectionResultsResponse>(`/api/selection-results?limit=${limit}`);
}

export function getSelectionResult(executionKey: string): Promise<SelectionResultDetailResponse> {
  return requestJson<SelectionResultDetailResponse>(
    `/api/selection-results/${encodeURIComponent(executionKey)}`,
  );
}

export function deleteSelectionResults(executionKeys?: string[]): Promise<DeleteResultsResponse> {
  return requestJson<DeleteResultsResponse>("/api/selection-results", {
    method: "DELETE",
    body: executionKeys ? JSON.stringify({ execution_keys: executionKeys }) : undefined,
  });
}

export function deleteSelectionResult(executionKey: string): Promise<DeleteResultsResponse> {
  return requestJson<DeleteResultsResponse>(
    `/api/selection-results/${encodeURIComponent(executionKey)}`,
    { method: "DELETE" },
  );
}
