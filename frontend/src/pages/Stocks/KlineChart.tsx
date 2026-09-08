import { useEffect, useRef } from "react";
import { dispose, init, registerIndicator } from "klinecharts";
import type { Chart, KLineData, Period } from "klinecharts";
import type { KlineBar, KlineResponse } from "../../types/kline";

const ZX_SHORT_COLOR = "#f5a623"; // 短期趋势线
const ZX_LONG_COLOR = "#b45fd9"; // 多空线
const DEFAULT_SUB_INDICATORS = ["VOL", "MACD"];
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
  // M12：千元 amount 不映射 turnover（KLineData 约定字段），成交额只走信息栏
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
  /** VOL/MACD 之外的副图内置指标（spec §5.3，可加可删）。 */
  subIndicators: string[];
}

/** v10 数据入口是 setDataLoader + setSymbol/setPeriod（无 applyNewData，B3）；
 *  请求由 useKline 发起，本组件同步 ref 数据并驱动 loader。所有指标创建均以
 *  chart.getIndicators() 为真相源做幂等检查（StrictMode/重挂载/热更皆不重复）。 */
export function KlineChart({ payload, subIndicators }: KlineChartProps) {
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
    // 涨红跌绿（N12）：蜡烛实体/影线/边框与指标量柱两套样式路径，键名以 10.0.3 Styles 为准
    chart.setStyles({
      candle: {
        bar: {
          upColor: "#ef232a", downColor: "#14b143",
          upBorderColor: "#ef232a", downBorderColor: "#14b143",
          upWickColor: "#ef232a", downWickColor: "#14b143",
        },
      },
      indicator: { bars: [{ upColor: "#ef232a", downColor: "#14b143" }] },
    });

    // 主图叠加：MA(34/55/144/233) + ZX——v10 主图叠加用 paneId: 'candle_pane'
    // （官方文档模式，放指标对象内）；幂等：已存在则跳过
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

  // 副图指标（VOL/MACD 默认 + extras）：以图表实例为真相源幂等增删（N12）
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const target = [...DEFAULT_SUB_INDICATORS, ...subIndicators];
    const indicators = chart.getIndicators();
    const existingNames = indicators.map((i) => i.name);
    // 删除循环只作用于副图窗格（非 candle_pane）——否则会把主图叠加的 MA/ZX 一并移除
    for (const ind of indicators.filter((i) => i.paneId !== "candle_pane")) {
      if (!target.includes(ind.name)) chart.removeIndicator({ name: ind.name });
    }
    for (const name of target) {
      if (!existingNames.includes(name)) {
        const paneId = chart.createIndicator(name, false);
        if (paneId != null) chart.setPaneOptions({ id: paneId, height: 90 });
      }
    }
  }, [subIndicators, payload]);

  return <div ref={containerRef} className="kline-chart-container" />;
}
