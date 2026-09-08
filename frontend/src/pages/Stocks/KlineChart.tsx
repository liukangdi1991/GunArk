import { useEffect, useRef } from "react";
import { dispose, init, registerIndicator } from "klinecharts";
import type { Chart, KLineData } from "klinecharts";
import type { KlineBar, KlineResponse } from "../../types/kline";

const ZX_SHORT_COLOR = "#f5a623"; // 短期趋势线
const ZX_LONG_COLOR = "#b45fd9"; // 多空线
const DEFAULT_SUB_INDICATORS = ["VOL", "MACD"];

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
        const bar = k as unknown as KlineBar; // d.ts: KLineData 与 KlineBar 无充分重叠，经 unknown 断言
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

export interface KlineChartProps {
  payload: KlineResponse | null;
  /** VOL/MACD 之外的副图内置指标（spec §5.3，可加可删）。 */
  subIndicators: string[];
}

/** v10 数据入口是 setDataLoader/resetData（无 applyNewData，B3）；
 *  请求由 useKline 发起，本组件只注册一次 loader 同步 ref 数据。 */
export function KlineChart({ payload, subIndicators }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const dataRef = useRef<KlineResponse | null>(payload);
  const createdSubsRef = useRef<string[]>([]);

  useEffect(() => {
    dataRef.current = payload;
    chartRef.current?.resetData(); // 触发 loader 'init' 重取 ref 数据
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

    // 主图叠加：MA(34/55/144/233) + ZX（isStack=true 即落在蜡烛主图，v10 无第三参 paneOptions）
    chart.createIndicator({ name: "MA", calcParams: [34, 55, 144, 233] }, true);
    chart.createIndicator("ZX", true);
    // 副图默认 VOL + MACD（v10 高度经 setPaneOptions 设置）
    const volPaneId = chart.createIndicator("VOL", false);
    if (volPaneId != null) chart.setPaneOptions({ id: volPaneId, height: 90 });
    const macdPaneId = chart.createIndicator("MACD", false);
    if (macdPaneId != null) chart.setPaneOptions({ id: macdPaneId, height: 90 });
    createdSubsRef.current = [...DEFAULT_SUB_INDICATORS];

    chart.setDataLoader({
      getBars: ({ type, callback: done }) => { // v10 callback 在 params 内（d.ts DataLoaderGetBarsParams）
        if (type !== "init") {
          done([], { forward: false, backward: false }); // 全量数据，无分页（M8）
          return;
        }
        const resp = dataRef.current;
        done(resp ? toKlineData(resp.bars) : [], { forward: false, backward: false });
      },
    });

    return () => {
      dispose(container); // M14：StrictMode 双挂载不残留画布
      chartRef.current = null;
      createdSubsRef.current = [];
    };
  }, []);

  // 副图指标增删（N12）：createIndicator/removeIndicator，空窗自动销毁
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const target = [...DEFAULT_SUB_INDICATORS, ...subIndicators];
    for (const name of createdSubsRef.current) {
      if (!target.includes(name)) chart.removeIndicator({ name });
    }
    for (const name of target) {
      if (!createdSubsRef.current.includes(name)) {
        const paneId = chart.createIndicator(name, false);
        if (paneId != null) chart.setPaneOptions({ id: paneId, height: 90 });
      }
    }
    createdSubsRef.current = target;
  }, [subIndicators]);

  return <div ref={containerRef} className="kline-chart-container" />;
}
