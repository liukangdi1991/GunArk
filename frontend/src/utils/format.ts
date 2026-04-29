export function formatNumber(value: unknown, fractionDigits = 2): string {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return num.toFixed(fractionDigits);
}

export function formatMoney(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return num.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPercent(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function signedClassName(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) {
    return "";
  }
  return num > 0 ? "value-positive" : "value-negative";
}

export function compactStrategyNames(strategies: string[] = []): string {
  if (!strategies.length) {
    return "默认策略";
  }
  if (strategies.length <= 2) {
    return strategies.join("、");
  }
  return `${strategies.slice(0, 2).join("、")} 等 ${strategies.length} 个`;
}
