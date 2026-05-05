export const CAPITAL_MODE_OPTIONS = [
  { label: "不限资金", value: "unlimited_cash" },
  { label: "现金约束", value: "realistic" },
];

export function capitalModeLabel(value?: string | null) {
  const matched = CAPITAL_MODE_OPTIONS.find((item) => item.value === value);
  return matched?.label || value || "-";
}
