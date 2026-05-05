import { ArrowDownOutlined, ArrowUpOutlined, MinusOutlined } from "@ant-design/icons";
import { formatMoney, formatPercent, parseFiniteNumber, signedClassName } from "../../../utils/format";

export function formatPlainPercent(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "-";
  }
  return `${num.toFixed(2)}%`;
}

export function winRateClassName(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "";
  }
  if (num >= 50) {
    return "win-rate-good";
  }
  if (num >= 30) {
    return "win-rate-mid";
  }
  return "win-rate-low";
}

export function SignedValue({
  value,
  type,
}: {
  value: unknown;
  type: "money" | "percent";
}) {
  const num = parseFiniteNumber(value);
  const Icon = num === null || num === 0 ? MinusOutlined : num > 0 ? ArrowUpOutlined : ArrowDownOutlined;
  return (
    <span className={`signed-value ${signedClassName(value)}`}>
      <Icon />
      {type === "money" ? formatMoney(value) : formatPercent(value)}
    </span>
  );
}
