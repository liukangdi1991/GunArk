export type ExecutionStatus = "submitted" | "queued" | "running" | "cancelling" | "cancelled" | "success" | "failed";

export interface Execution {
  execution_id: string;
  execution_type: string;
  type?: string;
  status: ExecutionStatus;
  progress_current: number;
  progress_total: number;
  progress_message: string;
  error_message?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  result_url?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ExecutionConsoleResponse {
  execution: Execution;
  offset: number;
  text: string;
  more: boolean;
}

export type ExecutionRequestType =
  | "selection_latest"
  | "selection_single"
  | "selection_batch"
  | "backtest"
  | "backtest_from_selection"
  | "selection_backtest"
  | "market_bars_sync";

export interface ExecutionRequest {
  type: ExecutionRequestType;
  params: Record<string, unknown>;
}

export interface ExecutionSubmitResponse extends Execution {
  console_url: string;
}
