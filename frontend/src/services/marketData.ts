import { requestJson } from "./apiClient";
import type { MarketDataStatus, TradingDatesResponse } from "../types/marketData";
import type { AdjustMode, KlinePeriod, KlineResponse } from "../types/kline";

let tradingDatesCache: TradingDatesResponse | null = null;
let tradingDatesRequest: Promise<TradingDatesResponse> | null = null;

export function listTradingDates(): Promise<TradingDatesResponse> {
  if (tradingDatesCache) {
    return Promise.resolve(tradingDatesCache);
  }
  if (!tradingDatesRequest) {
    tradingDatesRequest = requestJson<TradingDatesResponse>("/api/market-data/trading-dates")
      .then((payload) => {
        tradingDatesCache = payload;
        return payload;
      })
      .finally(() => {
        tradingDatesRequest = null;
      });
  }
  return tradingDatesRequest;
}

export function clearTradingDatesCache() {
  tradingDatesCache = null;
  tradingDatesRequest = null;
}

export function getMarketDataStatus(): Promise<MarketDataStatus> {
  return requestJson<MarketDataStatus>("/api/market-data/status");
}

export interface JobSubmitResponse {
  data: { job_id: string };
}

export function submitMarketBackfill(): Promise<JobSubmitResponse> {
  return requestJson<JobSubmitResponse>("/api/market-data/backfill", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function confirmDoubtfulDays(): Promise<{ status: string; confirmed: string[] }> {
  return requestJson<{ status: string; confirmed: string[] }>(
    "/api/market-data/confirm-doubtful",
    { method: "POST" },
  );
}

export async function getKline(
  code: string,
  period: KlinePeriod,
  adjust: AdjustMode,
  options?: { signal?: AbortSignal },
): Promise<KlineResponse> {
  const params = new URLSearchParams({ period, adjust });
  return requestJson<KlineResponse>(`/api/stocks/${code}/kline?${params.toString()}`, {
    signal: options?.signal,
  });
}
