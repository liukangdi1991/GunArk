import { requestJson } from "./apiClient";
import type { BacktestReportResponse, BacktestResultsResponse } from "../types/backtest";
import type { DeleteResultsResponse } from "./selections";

export function listBacktestResults(limit = 50): Promise<BacktestResultsResponse> {
  return requestJson<BacktestResultsResponse>(`/api/backtest-results?limit=${limit}`);
}

export function getBacktestReport(executionKey: string): Promise<BacktestReportResponse> {
  return requestJson<BacktestReportResponse>(
    `/api/backtest-results/${encodeURIComponent(executionKey)}/report`,
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
