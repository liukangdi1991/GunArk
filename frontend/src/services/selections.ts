import { requestJson } from "./apiClient";
import type {
  SelectionPicksPage,
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

export interface SelectionPicksQuery {
  offset: number;
  limit: number;
  strategies?: string[];
  dates?: string[];
  sortBy?: string;
  sortDir?: "asc" | "desc";
}

/** 服务端分页明细：筛选/排序/切片都在后端完成，响应只含当前页。 */
export function getSelectionPicks(
  executionKey: string,
  query: SelectionPicksQuery,
  signal?: AbortSignal,
): Promise<SelectionPicksPage> {
  const params = new URLSearchParams({
    offset: String(query.offset),
    limit: String(query.limit),
  });
  for (const strategy of query.strategies ?? []) {
    params.append("strategies", strategy);
  }
  for (const day of query.dates ?? []) {
    params.append("dates", day);
  }
  if (query.sortBy) {
    params.set("sort_by", query.sortBy);
    params.set("sort_dir", query.sortDir ?? "asc");
  }
  return requestJson<SelectionPicksPage>(
    `/api/selection-results/${encodeURIComponent(executionKey)}/picks?${params.toString()}`,
    { signal },
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
