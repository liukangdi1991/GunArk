import dayjs from "dayjs";
import type { Dayjs } from "dayjs";

export function formatPickerDate(value?: Dayjs) {
  return value ? value.format("YYYYMMDD") : null;
}

export function toDayjs(value?: string | null) {
  return value ? dayjs(value) : undefined;
}

export function makeDisabledNonTradingDate(tradingDates: string[]) {
  const set = new Set(tradingDates);
  return (current: Dayjs) => {
    if (!current || set.size === 0) {
      return false;
    }
    return !set.has(current.format("YYYY-MM-DD"));
  };
}

export function latestTradingDate(tradingDates: string[]) {
  return tradingDates[tradingDates.length - 1];
}

export function firstTradingDateOfLatestMonth(tradingDates: string[]) {
  const latest = latestTradingDate(tradingDates);
  if (!latest) {
    return undefined;
  }
  const monthPrefix = latest.slice(0, 7);
  return tradingDates.find((item) => item.startsWith(monthPrefix)) || latest;
}
