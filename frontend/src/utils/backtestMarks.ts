import type { BacktestMark } from "../types/backtest";
import type { BarMark, KlineBar } from "../types/kline";

/** 与 klinecharts 的 TooltipLegend 同形（只依赖结构，避免把图表库拖进工具模块）。 */
export interface MarkLegend {
  title: { text: string; color: string };
  value: { text: string; color: string };
}

/**
 * 把回测买卖标记归并到 K 线 bar 上（spec: 2026-09-15-backtest-marks-on-kline-design §5.3）。
 *
 * 规则与周期无关：一笔成交归到**第一根 `bar.date >= 成交日`** 的 bar——
 * 日线上退化为精确匹配，周/月线（bar.date 是该周期最后一个交易日）即「落到所属周期那根」。
 * 位置只由 bar 决定（买取下沿 low、卖取上沿 high），与复权口径无关（spec §4.3：
 * 图表是前复权、成交价是未复权，按价格落点必然错位）。
 *
 * 返回新数组与新 bar 对象（不改写入参）。
 */
export function attachMarks(bars: KlineBar[], marks: BacktestMark[]): KlineBar[] {
  if (bars.length === 0 || marks.length === 0) return bars;

  const buckets = new Map<number, { buy: BacktestMark[]; sell: BacktestMark[] }>();
  for (const mark of marks) {
    const index = firstBarIndexOnOrAfter(bars, mark.date);
    if (index < 0) continue; // 成交日晚于最后一根：丢弃（回测与K线同源，正常不可达）
    const bucket = buckets.get(index) ?? { buy: [], sell: [] };
    (mark.side === "sell" ? bucket.sell : bucket.buy).push(mark);
    buckets.set(index, bucket);
  }
  if (buckets.size === 0) return bars;

  return bars.map((bar, index) => {
    const bucket = buckets.get(index);
    if (!bucket) return bar;
    const next: KlineBar = { ...bar };
    if (bucket.buy.length > 0) next.bt_buy = toBarMark("B", bucket.buy, bar.low);
    if (bucket.sell.length > 0) next.bt_sell = toBarMark("S", bucket.sell, bar.high);
    return next;
  });
}

/** 二分找第一根 `date >= target` 的 bar（bars 按日期升序；找不到返回 -1）。 */
function firstBarIndexOnOrAfter(bars: KlineBar[], target: string): number {
  let low = 0;
  let high = bars.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if ((bars[mid].date ?? "") >= target) {
      found = mid;
      high = mid - 1;
    } else {
      low = mid + 1;
    }
  }
  return found;
}

function toBarMark(letter: string, items: BacktestMark[], value: number | null): BarMark {
  const sorted = [...items].sort((a, b) => (a.strategy > b.strategy ? 1 : a.strategy < b.strategy ? -1 : 0));
  return {
    label: sorted.length > 1 ? `${letter}×${sorted.length}` : letter,
    count: sorted.length,
    value: value ?? null,
    items: sorted,
  };
}

/** 按策略 id 白名单过滤标记（null/空 = 不过滤，原样返回）。spec M8：图上只显示勾选的策略。 */
export function filterMarksByStrategies(marks: BacktestMark[], strategies: readonly string[] | null): BacktestMark[] {
  if (!strategies || strategies.length === 0) return marks;
  const allow = new Set(strategies);
  return marks.filter((mark) => allow.has(mark.strategy));
}

/** 提示条计数：按**笔数**统计（不是 bar 数）。 */
export function countMarks(marks: BacktestMark[]): { buy: number; sell: number } {
  let buy = 0;
  let sell = 0;
  for (const mark of marks) {
    if (mark.side === "sell") sell++;
    else buy++;
  }
  return { buy, sell };
}

/** 单笔回测成交：`B 78.66×600 · 单针下20`；卖出带收益率。 */
export function barMarkText(mark: BacktestMark, letter: string): string {
  const parts = [`${letter} ${fmtPrice(mark.price)}×${mark.shares ?? "—"}`];
  if (mark.side === "sell" && mark.return_pct != null) parts.push(`${mark.return_pct.toFixed(2)}%`);
  parts.push(mark.strategy_name || mark.strategy);
  return parts.join(" · ");
}

/** 弹框里的回测行：买段在前、卖段在后，段内按日期序，多笔用 ` / ` 分隔；无标记返回 null。 */
export function barMarkLegend(bar: KlineBar, color: string): MarkLegend | null {
  const segments: string[] = [];
  if (bar.bt_buy?.items.length) {
    segments.push(bar.bt_buy.items.map((mark) => barMarkText(mark, "B")).join(" / "));
  }
  if (bar.bt_sell?.items.length) {
    segments.push(bar.bt_sell.items.map((mark) => barMarkText(mark, "S")).join(" / "));
  }
  if (segments.length === 0) return null;
  return {
    title: { text: "回测", color },
    value: { text: segments.join(" / "), color },
  };
}

function fmtPrice(value: number | null): string {
  return value == null ? "—" : value.toFixed(2);
}
