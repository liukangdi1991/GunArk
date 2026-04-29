export interface TradingDatesResponse {
  from: string | null;
  to: string | null;
  effective_to?: string | null;
  latest_data_date?: string | null;
  count: number;
  dates: string[];
}

export interface MarketDataStatus {
  data_dir: string;
  stocklist: string;
  stock_count: number;
  local_file_count: number;
  latest_date: string | null;
}
