export function parseFiniteNumber(value: unknown): number | null {
  if (typeof value === "string") {
    const text = value.trim();
    const numpyMatch = text.match(/^np\.(?:float\d*|int\d*)\(([-+0-9.eE]+)\)$/);
    const num = Number(numpyMatch ? numpyMatch[1] : text);
    return Number.isFinite(num) ? num : null;
  }

  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

export function formatNumber(value: unknown, fractionDigits = 2): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "-";
  }
  return num.toFixed(fractionDigits);
}

export function formatMoney(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "-";
  }
  return num.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPercent(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null) {
    return "-";
  }
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function signedClassName(value: unknown): string {
  const num = parseFiniteNumber(value);
  if (num === null || num === 0) {
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

export function formatDateRange(from?: string | null, to?: string | null): string {
  const cleanFrom = String(from || "").trim();
  const cleanTo = String(to || "").trim();
  if (!cleanFrom && !cleanTo) {
    return "-";
  }
  if (!cleanFrom) {
    return cleanTo;
  }
  if (!cleanTo || cleanFrom === cleanTo) {
    return cleanFrom;
  }
  return `${cleanFrom} ~ ${cleanTo}`;
}
