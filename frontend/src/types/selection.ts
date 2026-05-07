import type { Strategy } from "./strategy";

export interface SelectionSummary {
  strategy: string;
  date: string;
  count: number;
  elapsed_seconds: number;
}

export interface SelectionResult {
  execution_key: string;
  created_at?: string;
  finished_at?: string;
  selection_date: string;
  strategies: string[];
  strategy_snapshots: Strategy[];
  data_dir?: string;
  signal_file?: string;
  status: string;
  summary: SelectionSummary[];
  object_dir_key?: string;
  selection_group_key?: string;
  selection_from?: string | null;
  selection_to?: string | null;
  selection_execution_keys?: string[];
}

export interface SelectionPick {
  execution_key: string;
  strategy: string;
  date: string;
  code: string;
  name: string;
  industry: string;
}

export interface SelectionArtifact {
  artifact_scope: string;
  artifact_type: string;
  storage_key: string;
  mime_type: string;
  size_bytes: number;
  checksum: string;
  created_at: string;
}

export interface SelectionResultsResponse {
  results: SelectionResult[];
}

export interface SelectionResultDetailResponse {
  result: SelectionResult;
  artifacts: SelectionArtifact[];
  picks: SelectionPick[];
}
