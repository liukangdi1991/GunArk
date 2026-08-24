export const TRADE_STRATEGY_OPTIONS = [
  { label: "不限资金 + 多空线止损", value: "long_term_bull_bear_stop" },
  { label: "不限资金 + 10日低点止损", value: "ten_day_low_stop" },
  { label: "超短线（信号日收盘买入，次日收盘卖出）", value: "ultra_short" },
];

export function tradeStrategyLabel(value?: string | null) {
  const matched = TRADE_STRATEGY_OPTIONS.find((item) => item.value === value);
  return matched?.label || value || "-";
}
