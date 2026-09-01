export interface TradingDatesResponse {
  from: string | null;
  to: string | null;
  count: number;
  dates: string[];
}

export interface CalendarStatus {
  status: string | null;
  max_trade_date: string | null;
  covers_today: boolean;
  job_id: string | null;
}

export interface FreshnessStatus {
  trusted_through: string | null;
  latest_tradeable: string | null;
  stale_days: number | null;
  total_missing_days: number | null;
}

export interface BarsSyncStatus {
  status: string | null;
  finished_at: string | null;
  error_message: string | null;
  job_id: string | null;
}

export interface SkippedCode {
  code: string;
  attempts: number;
  last_error: string | null;
}

export interface CoverageStatus {
  missing_codes: number;
  skipped: SkippedCode[];
}

export interface ConsistencyStatus {
  ledger_suspect: boolean;
  marker_mismatch: boolean;
  doubtful_days: string[];
}

export interface StorageStatus {
  data_dir: string;
  stocklist: string;
  stock_count: number;
  local_file_count: number;
  latest_date: string | null;
}

export interface MarketDataStatus {
  calendar: CalendarStatus;
  freshness: FreshnessStatus;
  bars_sync: BarsSyncStatus;
  coverage: CoverageStatus;
  consistency: ConsistencyStatus;
  storage: StorageStatus;
}
