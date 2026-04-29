import { requestJson } from "./apiClient";
import type { MarketDataStatus, TradingDatesResponse } from "../types/marketData";

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
