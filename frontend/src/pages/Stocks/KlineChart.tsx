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

const UP = "#ef232a";
const DOWN = "#14b143";
const ZX_SHORT = "#f5a623"; // 短期趋势线（用户 TDX 白黄紫绿之黄）
const ZX_LONG = "#b45fd9"; // 多空线
const NEUTRAL = "#606a78"; // 弹框中性文字色（TooltipLegendChild.color 必填）
const CROSS_UP = "#2196f3"; // 知行MACD DIFF 上穿零轴（TDX COLOR0000FF）
const CROSS_DOWN = "#8bc34a"; // DIFF 下穿零轴（COLORLIGREEN）
const XPSD_SHORT = "#00c0c0"; // 洗盘线短期（青）
const XPSD_LONG = "#ef232a"; // 洗盘线长期（红）

/** 后端已按周期聚合完毕；此处 Period 仅作 v10 loader 门控与轴刻度形态。 */
const PERIOD_SETTINGS: Record<KlineResponse["period"], Period> = {
  daily: { type: "day", span: 1 },
  weekly: { type: "week", span: 1 },
  monthly: { type: "month", span: 1 },
};

/** 主图叠加指标（MA 由 klinecharts 内置提供；ZX 为自定义）。 */
const MAIN_OVERLAYS: { name: string; calcParams?: number[] }[] = [
  { name: "MA", calcParams: [34, 55, 144, 233] },
  { name: "ZX" },
];
/** #3：默认副图仅成交量，其余走「副图指标」菜单扩展。 */
const DEFAULT_SUB_INDICATORS = ["VOL"];

/* ---- 指标注册（模块级幂等，StrictMode/热更安全） ---- */

let zxRegistered = false;

/** ZX：calc 直读 bar 附带的 zx_short/zx_long（后端已算好，null 跳过）。 */
function ensureZxRegistered() {
  if (zxRegistered) return;
  registerIndicator<{ zx_short: number | null; zx_long: number | null }>({
    name: "ZX",
    shortName: "短期趋势线/多空线",
    series: "price",
    precision: 2,
    figures: [
      { key: "zx_short", title: "短期趋势线: ", type: "line", styles: () => ({ color: ZX_SHORT }) },
      { key: "zx_long", title: "多空线: ", type: "line", styles: () => ({ color: ZX_LONG }) },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        // toKlineData 已把自定义字段附在 KLineData 上；KLineData 类型无法表达
        const bar = k as unknown as KlineBar;
        return { zx_short: bar.zx_short ?? null, zx_long: bar.zx_long ?? null };
      }),
  });
  zxRegistered = true;
}

let brickRegistered = false;
const BRICK_COLORS: Record<string, string> = {
  red: UP, // 上行
  green: DOWN, // 下行
  orange: "#ff8000", // 回踩后再上行且力度不弱（着色优先级最高）
};

/** 砖形图 MT（用户 TDX 原文 STICKLINE 着色）：后端派生 mt/mt_prev/mt_color，
 *  rect figure 用 attrs 覆写 y/height 画「前值→现值」楼梯砖。 */
function ensureBrickRegistered() {
  if (brickRegistered) return;
  registerIndicator<{ mt: number | null; mt_prev: number | null; mt_color: string | null }>({
    name: "MTBRICK",
    shortName: "砖形图MT",
    precision: 2,
    figures: [
      {
        key: "mt",
        title: "MT: ",
        type: "rect",
        baseValue: 0,
        attrs: (p) => {
          const cur = p.coordinate?.current?.mt;
          const prev = p.coordinate?.prev?.mt;
          if (!Number.isFinite(cur) || !Number.isFinite(prev)) return { width: 0 };
          return {
            y: Math.min(cur, prev),
            height: Math.max(1, Math.abs(cur - prev)),
            width: Math.max(1, (p.barSpace?.gapBar ?? 8) * 0.9),
          };
        },
        styles: (p) => ({
          color: BRICK_COLORS[p.data?.current?.mt_color ?? ""] ?? NEUTRAL,
        }),
      },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        const bar = k as unknown as KlineBar;
        return { mt: bar.mt ?? null, mt_prev: bar.mt_prev ?? null, mt_color: bar.mt_color ?? null };
      }),
  });
  brickRegistered = true;
}

let xpsdRegistered = false;

/** 知行洗盘线：短/长两线 + 四类买点信号柱（后端已算好）。 */
function ensureXpsdRegistered() {
  if (xpsdRegistered) return;
  registerIndicator<{
    xpsd_short: number | null;
    xpsd_long: number | null;
    xpsig_zero: number | null;
    xpsig_w20: number | null;
    xpsig_xlong: number | null;
    xpsig_xmid: number | null;
  }>({
    name: "XPSD",
    shortName: "知行洗盘线",
    precision: 2,
    figures: [
      { key: "xpsd_short", title: "短期: ", type: "line", styles: () => ({ color: XPSD_SHORT }) },
      { key: "xpsd_long", title: "长期: ", type: "line", styles: () => ({ color: XPSD_LONG }) },
      { key: "xpsig_zero", title: "四线归零: ", type: "bar", baseValue: 0, styles: () => ({ color: "#0000ff" }) },
      { key: "xpsig_w20", title: "线下20: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ffff" }) },
      { key: "xpsig_xlong", title: "穿红线: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ff00" }) },
      { key: "xpsig_xmid", title: "穿黄线: ", type: "bar", baseValue: 0, styles: () => ({ color: "#ff9150" }) },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        const bar = k as unknown as KlineBar;
        return {
          xpsd_short: bar.xpsd_short ?? null,
          xpsd_long: bar.xpsd_long ?? null,
          xpsig_zero: bar.xpsig_zero ?? null,
          xpsig_w20: bar.xpsig_w20 ?? null,
          xpsig_xlong: bar.xpsig_xlong ?? null,
          xpsig_xmid: bar.xpsig_xmid ?? null,
        };
      }),
  });
  xpsdRegistered = true;
}

let ztMacdRegistered = false;

interface ZtMacdResult {
  dif: number;
  dea: number;
  macd_red: number | null;
  macd_green: number | null;
  macd_cross: string | null;
  macd: number;
}

/** 知行MACD：标准 DIF/DEA + 红绿柱分离 + 零轴穿越加粗高亮（用户 TDX 原文）。 */
function ensureZtMacdRegistered() {
  if (ztMacdRegistered) return;
  registerIndicator<ZtMacdResult>({
    name: "ZTMACD",
    shortName: "知行MACD",
    precision: 3,
    figures: [
      { key: "dif", title: "DIF: ", type: "line" },
      { key: "dea", title: "DEA: ", type: "line" },
      { key: "macd_red", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: UP }) },
      { key: "macd_green", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: DOWN }) },
      {
        key: "macd_cross",
        title: "零轴穿越: ",
        type: "bar",
        baseValue: 0,
        attrs: (p) => (p.data?.current?.macd_cross ? { width: Math.max(1, p.barSpace.gapBar * 1.6) } : { width: 0 }),
        styles: (p) => ({
          color: p.data?.current?.macd_cross === "up" ? CROSS_UP : CROSS_DOWN,
        }),
      },
    ],
    calc: (dataList): ZtMacdResult[] => {
      // TDX EMA(C,12)/EMA(C,26)/EMA(DIF,9)；MACD=2*(DIF-DEA)
      const closes = dataList.map((k) => Number(k.close) || 0);
      const ema = (vals: number[], n: number): number[] => {
        const alpha = 2 / (n + 1);
        let prev = vals[0] ?? 0;
        return vals.map((v) => {
          prev = v * alpha + prev * (1 - alpha);
          return prev;
        });
      };
      const e12 = ema(closes, 12);
      const e26 = ema(closes, 26);
      const dif = e12.map((v, i) => v - e26[i]);
      const dea = ema(dif, 9);
      return dif.map((v, i): ZtMacdResult => {
        const macd = 2 * (v - dea[i]);
        const prevDif = i > 0 ? dif[i - 1] : 0;
        const cross =
          prevDif <= 0 && v > 0 ? "up" : prevDif >= 0 && v < 0 ? "down" : "";
        return {
          dif: v,
          dea: dea[i],
          macd_red: macd >= 0 ? macd : null,
          macd_green: macd < 0 ? macd : null,
          macd_cross: cross || null,
          macd,
        };
      });
    },
  });
  ztMacdRegistered = true;
}

function ensureAllRegistered() {
  ensureZxRegistered();
  ensureBrickRegistered();
  ensureXpsdRegistered();
  ensureZtMacdRegistered();
}

/* ---- 工具 ---- */

/** M12：自定义字段随行，供十字光标弹框与自定义指标 calc 使用。 */
function toKlineData(bars: KlineBar[]): KLineData[] {
  return bars.map(
    (b) =>
      ({
        timestamp: b.timestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
        amount: b.amount,
        zx_short: b.zx_short,
        zx_long: b.zx_long,
        mt: b.mt,
        mt_prev: b.mt_prev,
        mt_color: b.mt_color,
        xpsd_short: b.xpsd_short,
        xpsd_long: b.xpsd_long,
        xpsig_zero: b.xpsig_zero,
        xpsig_w20: b.xpsig_w20,
        xpsig_xlong: b.xpsig_xlong,
        xpsig_xmid: b.xpsig_xmid,
      }) as unknown as KLineData,
  );
}

/** 成交额按千元输入（后端 tushare amount 单位：千元）→ 元 ladder：亿/万元。 */
function formatAmount(kQian: number | null | undefined): string {
  if (kQian == null || !Number.isFinite(kQian)) return "—";
  const yuan = kQian * 1000;
  const abs = Math.abs(yuan);
  if (abs >= 1e8) return `${(yuan / 1e8).toFixed(2)}亿元`;
  if (abs >= 1e4) return `${(yuan / 1e4).toFixed(0)}万元`;
  return `${yuan.toFixed(0)}元`;
}

/** #5：成交量坐标轴/legend 中文数量级（万/千万/亿）。 */
function formatVol(value: string | number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (abs >= 1e7) return `${(n / 1e7).toFixed(2)}千万`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return `${n}`;
}

function fmtP(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(2);
}

/** 主图中文十字光标弹框（M11：开盘/最高/最低/收盘/涨跌幅/成交量/成交额）。 */
function candleLegends(data: { prev: unknown; current: unknown }): TooltipLegend[] {
  const cur = data.current as unknown as KlineBar | null;
  if (!cur || cur.close == null) return [];
  const prev = data.prev as unknown as KlineBar | null;
  let pct: number | null = null;
  if (cur.pre_close != null && cur.pre_close > 0) {
    pct = (cur.close - cur.pre_close) / cur.pre_close;
  } else if (prev && prev.close != null && prev.close > 0) {
    pct = (cur.close - prev.close) / prev.close;
  }
  const dirColor = pct == null ? NEUTRAL : pct >= 0 ? UP : DOWN;
  const pctText = pct == null ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(2)}%`;
  return [
    { title: { text: "开盘", color: NEUTRAL }, value: { text: fmtP(cur.open), color: NEUTRAL } },
    { title: { text: "最高", color: NEUTRAL }, value: { text: fmtP(cur.high), color: NEUTRAL } },
    { title: { text: "最低", color: NEUTRAL }, value: { text: fmtP(cur.low), color: NEUTRAL } },
    { title: { text: "收盘", color: dirColor }, value: { text: fmtP(cur.close), color: dirColor } },
    { title: { text: "涨跌幅", color: dirColor }, value: { text: pctText, color: dirColor } },
    { title: { text: "成交量(手)", color: NEUTRAL }, value: { text: formatVol(cur.volume ?? 0), color: NEUTRAL } },
    { title: { text: "成交额", color: NEUTRAL }, value: { text: formatAmount(cur.amount ?? null), color: NEUTRAL } },
  ];
}

function applySymbolAndPeriod(chart: Chart, payload: KlineResponse) {
  chart.setSymbol({ ticker: payload.code, symbol: payload.code, pricePrecision: 2, volumePrecision: 0 });
  chart.setPeriod(PERIOD_SETTINGS[payload.period]);
}

/* ---- 组件 ---- */

export interface KlineChartProps {
  payload: KlineResponse | null;
  mainOverlays: string[];
  subIndicators: string[];
}

export function KlineChart({ payload, mainOverlays, subIndicators }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const dataRef = useRef<KlineResponse | null>(payload);

  // 最新数据供 loader 回调读取
  useEffect(() => {
    dataRef.current = payload;
  }, [payload]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    ensureAllRegistered();
    const chart = init(container, { timezone: "Asia/Shanghai" });
    if (!chart) return;
    chartRef.current = chart;

    chart.setStyles({
      candle: {
        bar: {
          upColor: UP,
          downColor: DOWN,
          upBorderColor: UP,
          downBorderColor: DOWN,
          upWickColor: UP,
          downWickColor: DOWN,
        },
        tooltip: {
          showRule: "follow_cross",
          showType: "rect",
          legend: { template: candleLegends },
        },
      },
      indicator: {
        tooltip: { showRule: "follow_cross", showType: "rect" },
      },
    });
    chart.setFormatter({ formatBigNumber: formatVol });
    chart.setDecimalFold({ threshold: 10000, format: formatVol });

    for (const overlay of MAIN_OVERLAYS) {
      chart.createIndicator(
        { name: overlay.name, calcParams: overlay.calcParams, paneId: "candle_pane" },
        true,
      );
    }

    chart.setDataLoader({
      getBars: ({ type, callback: done }) => {
        if (type !== "init") {
          done([], { forward: false, backward: false });
          return;
        }
        const resp = dataRef.current;
        done(resp ? toKlineData(resp.bars) : [], { forward: false, backward: false });
      },
    });

    if (dataRef.current) applySymbolAndPeriod(chart, dataRef.current);

    return () => {
      dispose(container);
      chartRef.current = null;
    };
  }, []);

  // 主图/副图指标随菜单选择增删
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const mainTarget = new Set(mainOverlays);
    const subTarget = [...DEFAULT_SUB_INDICATORS, ...subIndicators];
    const subSet = new Set(subTarget);

    for (const overlay of MAIN_OVERLAYS) {
      const exists = chart.getIndicators().some((i) => i.name === overlay.name);
      if (mainTarget.has(overlay.name) && !exists) {
        chart.createIndicator(
          { name: overlay.name, calcParams: overlay.calcParams, paneId: "candle_pane" },
          true,
        );
      }
      if (!mainTarget.has(overlay.name) && exists) {
        chart.removeIndicator({ name: overlay.name });
      }
    }

    const existingSubs = new Set(
      chart.getIndicators().filter((i) => i.paneId !== "candle_pane").map((i) => i.name),
    );
    for (const name of existingSubs) {
      if (!subSet.has(name)) chart.removeIndicator({ name });
    }
    for (const name of subTarget) {
      if (!existingSubs.has(name)) {
        const paneId = chart.createIndicator(name, false);
        if (typeof paneId === "string") chart.setPaneOptions({ id: paneId, height: 90 });
      }
    }
  }, [subIndicators, mainOverlays, payload]);

  return <div ref={containerRef} className="kline-chart-container" />;
}
