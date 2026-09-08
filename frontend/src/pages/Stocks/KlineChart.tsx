import { useEffect, useRef } from "react";
import {
  dispose,
  init,
  registerIndicator,
  type Chart,
  type KLineData,
  type Period,
  type TooltipLegend,
} from "klinecharts";
import type { KlineBar, KlineResponse } from "../../types/kline";

const UP_COLOR = "#ef232a";
const DOWN_COLOR = "#14b143";
const ZX_SHORT_COLOR = "#f5a623"; // 短期趋势线
const ZX_LONG_COLOR = "#b45fd9"; // 多空线
const NEUTRAL = "#606a78"; // 弹框中性文字色（TooltipLegendChild.color 必填）
const DEFAULT_SUB_INDICATORS = ["VOL"]; // #3：默认仅成交量，其余走「副图指标」菜单扩展
const MAIN_OVERLAYS: { name: string; paneId: string; calcParams?: number[] }[] = [
  { name: "MA", paneId: "candle_pane", calcParams: [34, 55, 144, 233] },
  { name: "ZX", paneId: "candle_pane" },
];
/** 后端已按周期聚合完毕；此处 Period 仅作 v10 loader 门控与轴刻度形态。 */
const PERIOD_SETTINGS: Record<KlineResponse["period"], Period> = {
  daily: { type: "day", span: 1 },
  weekly: { type: "week", span: 1 },
  monthly: { type: "month", span: 1 },
};

let zxRegistered = false;

/** 自定义 ZX 指标：calc 直读 bar 附带的 zx_short/zx_long（后端已算好，null 跳过）。 */
function ensureZxRegistered() {
  if (zxRegistered) return;
  registerIndicator({
    name: "ZX",
    shortName: "短期趋势线/多空线",
    series: "price",
    precision: 2,
    figures: [
      { key: "zx_short", title: "短期趋势线: ", type: "line", styles: () => ({ color: ZX_SHORT_COLOR }) },
      { key: "zx_long", title: "多空线: ", type: "line", styles: () => ({ color: ZX_LONG_COLOR }) },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        const bar = k as unknown as KlineBar;
        return { zx_short: bar.zx_short ?? null, zx_long: bar.zx_long ?? null };
      }),
  });
  zxRegistered = true;
}

function toKlineData(bars: KlineBar[]): KLineData[] {
  // M12：千元 amount 不映射 turnover（KLineData 约定字段），成交额走十字光标弹框
  return bars.map((b) => ({
    timestamp: b.timestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
    zx_short: b.zx_short,
    zx_long: b.zx_long,
  })) as unknown as KLineData[]; // d.ts: KLineData.open 等为必填 number，可空值仅能经 unknown 断言
}

/** #5：万/亿 大数格式化（VOL legend 与坐标轴通用；单位「手」标注在 legend 标题）。 */
function formatWanYi(value: string | number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return `${n}`;
}

function fmtPrice(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(2);
}

/** #4 中文十字光标弹框内容：收盘按对昨涨跌着色，涨跌幅优先用 pre_close（交易所口径，
 *  none 档除权日正确）；pre_close 缺失回退序列比（图表序列本身是复权口径，等价）。 */
function candleTooltipLegends(data: {
  prev: unknown;
  current: unknown;
}): TooltipLegend[] {
  const cur = data.current as unknown as KlineBar | null;
  if (!cur || cur.close == null) return [];
  const prev = data.prev as unknown as KlineBar | null;
  let pct: number | null = null;
  if (cur.pre_close != null && cur.pre_close > 0) {
    pct = (cur.close - cur.pre_close) / cur.pre_close;
  } else if (prev && prev.close != null && prev.close > 0) {
    pct = (cur.close - prev.close) / prev.close; // 旧数据无 pre_close：回退序列比
  }
  const pctColor = pct == null ? undefined : pct >= 0 ? UP_COLOR : DOWN_COLOR;
  const pctText = pct == null ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(2)}%`;
  const closeColor = pct == null ? undefined : pct >= 0 ? UP_COLOR : DOWN_COLOR;
  return [
    { title: { text: "开盘", color: NEUTRAL }, value: { text: fmtPrice(cur.open), color: NEUTRAL } },
    { title: { text: "最高", color: NEUTRAL }, value: { text: fmtPrice(cur.high), color: NEUTRAL } },
    { title: { text: "最低", color: NEUTRAL }, value: { text: fmtPrice(cur.low), color: NEUTRAL } },
    { title: { text: "收盘", color: closeColor ?? NEUTRAL }, value: { text: fmtPrice(cur.close), color: closeColor ?? NEUTRAL } },
    { title: { text: "涨跌幅", color: NEUTRAL }, value: { text: pctText, color: pctColor ?? NEUTRAL } },
    { title: { text: "成交量(手)", color: NEUTRAL }, value: { text: formatWanYi(cur.volume ?? 0), color: NEUTRAL } },
    { title: { text: "成交额(千元)", color: NEUTRAL }, value: { text: formatWanYi(cur.amount ?? 0), color: NEUTRAL } },
  ];
}

/** v10 门控（dist/index.esm.js _processDataLoad）：_symbol 与 _period 双双有效
 *  才触发 getBars('init')。后调者触发加载；对象每次新建恒过 ChartImp 的 !== 守卫，
 *  故重复调用即整体重载（覆盖复权/周期/换股切换，R5：写 ref → setSymbol → setPeriod）。 */
function applySymbolAndPeriod(chart: Chart, payload: KlineResponse) {
  chart.setSymbol({
    ticker: payload.code,
    symbol: payload.code,
    pricePrecision: 2,
    volumePrecision: 0,
  });
  chart.setPeriod(PERIOD_SETTINGS[payload.period]);
}

export interface KlineChartProps {
  payload: KlineResponse | null;
  /** 主图叠加指标（MA/ZX，选中的才叠加，用户可通过「主图指标」菜单增删）。 */
  mainOverlays: string[];
  /** VOL 之外的副图内置指标（spec §5.3，可加可删）。 */
  subIndicators: string[];
}

/** v10 数据入口是 setDataLoader + setSymbol/setPeriod（无 applyNewData，B3）；
 *  请求由 useKline 发起，本组件同步 ref 数据并驱动 loader。所有指标创建均以
 *  chart.getIndicators() 为真相源做幂等检查（StrictMode/重挂载/热更皆不重复）。 */
export function KlineChart({ payload, mainOverlays, subIndicators }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const dataRef = useRef<KlineResponse | null>(payload);

  // 挂载后 payload 变更（周期/复权/换股）→ 整体重载。
  // 挂载首 render 时 chart 尚未创建（init 效应在后），此处早退；
  // 挂载路径的首次驱动在 init 效应末尾（G1 时序修复：页面条件渲染使挂载时
  // payload 已就绪且不再变化，本效应不会因数据到达而重跑）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !payload) return;
    dataRef.current = payload;
    applySymbolAndPeriod(chart, payload);
  }, [payload]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    ensureZxRegistered();
    const chart = init(container, { timezone: "Asia/Shanghai" }); // M11
    if (!chart) return;
    chartRef.current = chart;

    // 涨红跌绿（N12）：蜡烛实体/影线/边框与指标量柱两套样式路径；
    // #4：十字光标跟随弹框（follow_cross + rect）——指到才显示，离开即隐藏；
    // #5：万/亿大数格式化（VOL legend）+ 坐标轴 decimalFold
    chart.setStyles({
      candle: {
        bar: {
          upColor: UP_COLOR, downColor: DOWN_COLOR,
          upBorderColor: UP_COLOR, downBorderColor: DOWN_COLOR,
          upWickColor: UP_COLOR, downWickColor: DOWN_COLOR,
        },
        tooltip: {
          showRule: "follow_cross", // #4：不指不显示
          showType: "rect", // 同花顺式弹框
          legend: { template: candleTooltipLegends },
        },
      },
      indicator: {
        bars: [{ upColor: UP_COLOR, downColor: DOWN_COLOR }],
        tooltip: { showRule: "follow_cross", showType: "rect" },
      },
    });
    // #5：VOL legend 万/亿（默认 K/M/B）；坐标轴 decimalFold 折叠
    chart.setFormatter({ formatBigNumber: formatWanYi });
    chart.setDecimalFold({ threshold: 10000, format: formatWanYi });

    // 主图叠加：MA(34/55/144/233) + ZX——v10 主图叠加用 paneId: 'candle_pane'
    // （官方文档模式，放指标对象内）；实际增删由下方 sync 效应按用户选择管理
    const existing = chart.getIndicators().map((i) => i.name);
    for (const overlay of MAIN_OVERLAYS) {
      if (!existing.includes(overlay.name)) {
        chart.createIndicator(
          { name: overlay.name, calcParams: overlay.calcParams, paneId: overlay.paneId },
          true,
        );
      }
    }

    chart.setDataLoader({
      getBars: ({ type, callback: done }) => {
        // v10 callback 在 params 内（d.ts DataLoaderGetBarsParams）
        if (type !== "init") {
          done([], { forward: false, backward: false }); // 全量数据，无分页（M8）
          return;
        }
        const resp = dataRef.current;
        done(resp ? toKlineData(resp.bars) : [], { forward: false, backward: false });
      },
    });

    // 挂载路径首次驱动（G1 时序修复：页面条件渲染使挂载时 payload 已就绪且
    // 不再变化，[payload] 效应不会重跑——必须在此直接驱动一次 loader）
    if (dataRef.current) applySymbolAndPeriod(chart, dataRef.current);

    return () => {
      dispose(container); // M14：StrictMode 双挂载不残留画布
      chartRef.current = null;
    };
  }, []);

  // 指标同步（主图叠加 + 副图）：以图表实例为真相源幂等增删（N12）。
  // 主图叠加只动 candle_pane 内实例；副图删除只作用于非 candle_pane 窗格，
  // 避免误删主图 MA/ZX（浏览器验收发现的回归）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const mainTarget = new Set(mainOverlays);
    const subTarget = [...DEFAULT_SUB_INDICATORS, ...subIndicators];
    const subTargetSet = new Set(subTarget);
    for (const def of MAIN_OVERLAYS) {
      const exists = chart.getIndicators().some((i) => i.name === def.name);
      if (mainTarget.has(def.name) && !exists) {
        chart.createIndicator(
          { name: def.name, calcParams: def.calcParams, paneId: def.paneId },
          true,
        );
      }
      if (!mainTarget.has(def.name) && exists) {
        chart.removeIndicator({ name: def.name });
      }
    }
    const indicators = chart.getIndicators();
    const existingNames = indicators.map((i) => i.name);
    for (const ind of indicators.filter((i) => i.paneId !== "candle_pane")) {
      if (!subTargetSet.has(ind.name)) chart.removeIndicator({ name: ind.name });
    }
    for (const name of subTarget) {
      if (!existingNames.includes(name)) {
        const paneId = chart.createIndicator(name, false);
        if (paneId != null) chart.setPaneOptions({ id: paneId, height: 90 });
      }
    }
  }, [subIndicators, mainOverlays, payload]);

  return <div ref={containerRef} className="kline-chart-container" />;
}
