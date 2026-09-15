import type { BacktestMark } from "../types/backtest";
import type { BarMark, KlineBar } from "../types/kline";

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
