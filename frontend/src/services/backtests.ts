import { requestJson } from "./apiClient";
import type {
  BacktestDataset,
  BacktestReportResponse,
  BacktestResultsResponse,
} from "../types/backtest";
import type { Paged } from "../types/paging";
import type { DeleteResultsResponse } from "./selections";

export function listBacktestResults(limit = 50): Promise<BacktestResultsResponse> {
  return requestJson<BacktestResultsResponse>(`/api/backtest-results?limit=${limit}`);
}

export function getBacktestReport(executionKey: string): Promise<BacktestReportResponse> {
  return requestJson<BacktestReportResponse>(
    `/api/backtest-results/${encodeURIComponent(executionKey)}/report`,
  );
}

export interface BacktestRowsQuery {
  offset: number;
  limit: number;
  strategies?: string[];
  sortBy?: string;
  sortDir?: "asc" | "desc";
}

/** 明细分页（三个数据集共用）：筛选/排序/切片都在服务端完成，响应只含当前页。 */
export function getBacktestRows<T>(
  executionKey: string,
  dataset: BacktestDataset,
  query: BacktestRowsQuery,
  signal?: AbortSignal,
): Promise<Paged<T>> {
  const params = new URLSearchParams({
    offset: String(query.offset),
    limit: String(query.limit),
  });
  for (const strategy of query.strategies ?? []) {
    params.append("strategies", strategy);
  }
  if (query.sortBy) {
    params.set("sort_by", query.sortBy);
    params.set("sort_dir", query.sortDir ?? "asc");
  }
  return requestJson<Paged<T>>(
    `/api/backtest-results/${encodeURIComponent(executionKey)}/${dataset}?${params.toString()}`,
    { signal },
  );
}

export function deleteBacktestResults(executionKeys?: string[]): Promise<DeleteResultsResponse> {
  return requestJson<DeleteResultsResponse>("/api/backtest-results", {
    method: "DELETE",
    body: executionKeys ? JSON.stringify({ execution_keys: executionKeys }) : undefined,
  });
}

export function deleteBacktestResult(executionKey: string): Promise<DeleteResultsResponse> {
  return requestJson<DeleteResultsResponse>(
    `/api/backtest-results/${encodeURIComponent(executionKey)}`,
    { method: "DELETE" },
  );
}
