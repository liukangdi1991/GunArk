// 三种策略的买入已统一为「信号日当日收盘」（含当日收盘涨停拒绝），标签如实标注
// 2026-09-18 新增三种（2026-09-18-trade-strategy-stops-design.md）
export const TRADE_STRATEGY_OPTIONS = [
  { label: "不限资金 + 多空线止损（信号日收盘买入）", value: "long_term_bull_bear_stop" },
  { label: "不限资金 + 10日低点止损（信号日收盘买入）", value: "ten_day_low_stop" },
  { label: "超短线（信号日收盘买入，次日收盘卖出）", value: "ultra_short" },
  { label: "3%止损", value: "stop_3pct" },
  { label: "买入日最低价止损", value: "buy_day_low_stop" },
  { label: "绿砖止损", value: "green_brick_stop" },
  { label: "不限资金 + 趋势线止损", value: "trend_line_stop" },
];

export function tradeStrategyLabel(value?: string | null) {
  const matched = TRADE_STRATEGY_OPTIONS.find((item) => item.value === value);
  return matched?.label || value || "-";
}
