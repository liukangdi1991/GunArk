export const TRADE_STRATEGY_OPTIONS = [
  { label: "不限资金 + 多空线止损", value: "long_term_bull_bear_stop" },
  { label: "不限资金 + 10日低点止损", value: "ten_day_low_stop" },
];

export function tradeStrategyLabel(value?: string | null) {
  const matched = TRADE_STRATEGY_OPTIONS.find((item) => item.value === value);
  return matched?.label || value || "-";
}
