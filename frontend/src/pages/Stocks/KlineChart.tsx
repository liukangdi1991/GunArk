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
let brickRegistered = false;
let xpsdRegistered = false;
let ztMacdRegistered = false;

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

/** 砖形图 MT（用户 TDX 原文 STICKLINE 着色）：后端派生 mt/mt_prev/mt_color，
 *  前端 rect figure 用 attrs 覆写 y/height 画「前值→现值」楼梯砖；
 *  颜色：红=上行、绿=下行、橙=回踩后再上行且力度不弱（着色优先级最高）。 */
function ensureBrickRegistered() {
  if (brickRegistered) return;
  registerIndicator({
    name: "MTBRICK",
    shortName: "砖形图MT",
    precision: 2,
    figures: [
      {
        key: "mt",
        title: "MT: ",
        type: "rect",
        baseValue: 0,
        attrs: (p: {
          coordinate?: { current?: Record<string, number>; prev?: Record<string, number> };
          barSpace?: { gapBar: number };
        }) => {
          const cur = p.coordinate?.current?.mt;
          const prev = p.coordinate?.prev?.mt;
          if (!Number.isFinite(cur) || !Number.isFinite(prev)) return { width: 0 };
          return {
            y: Math.min(cur as number, prev as number),
            height: Math.max(1, Math.abs((cur as number) - (prev as number))),
            width: Math.max(1, (p.barSpace?.gapBar ?? 8) * 0.9),
          };
        },
        styles: (p: { data?: { current?: { mt_color?: string } } }) => ({
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

const BRICK_COLORS: Record<string, string> = {
  red: UP_COLOR,
  green: DOWN_COLOR,
  orange: "#ff8000",
};

/** 知行洗盘线（用户 TDX 原文）：只输出 短期(n1)/长期(n2) 两根线；
 *  中期/中长期仅参与信号计算（后端列仍在，但前端不画）；
 *  四类买入信号柱（−30，堆 0 轴下方）：四线归零(蓝)/线下20(青)/穿红线(绿)/穿黄线(橙)。 */
function ensureXpsdRegistered() {
  if (xpsdRegistered) return;
  registerIndicator({
    name: "XPSD",
    shortName: "知行洗盘线",
    precision: 2,
    figures: [
      { key: "xpsd_short", title: "短期: ", type: "line", styles: () => ({ color: "#00c0c0" }) },
      { key: "xpsd_long", title: "长期: ", type: "line", styles: () => ({ color: "#ef232a" }) },
      { key: "xpsig_zero", title: "四线归零买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#0000ff" }) },
      { key: "xpsig_w20", title: "线下20买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ffff" }) },
      { key: "xpsig_xlong", title: "穿红线买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ff00" }) },
      { key: "xpsig_xmid", title: "穿黄线买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#ff9150" }) },
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

const ZTMACD_CROSS_UP = "#2196f3"; // DIFF 上穿零轴：蓝（用户 TDX COLOR0000FF）
const ZTMACD_CROSS_DOWN = "#8bc34a"; // DIFF 下穿零轴：浅绿（COLORLIGREEN）

/** 知行MACD（用户 TDX 原文）：DIFF=EMA(C,12)−EMA(C,26)；DEA=EMA(DIFF,9)；
 *  MACD=2×(DIFF−DEA) COLORSTICK；DIFF 零轴穿越当日柱子加粗高亮（v10 bar figure
 *  支持 attrs.width 覆写，源码 barWidth = attrs.width ?? halfGapBar×2）。 */
function ensureZtMacdRegistered() {
  if (ztMacdRegistered) return;
  registerIndicator({
    name: "ZTMACD",
    shortName: "知行MACD",
    precision: 3,
    figures: [
      { key: "dif", title: "DIF: ", type: "line" },
      { key: "dea", title: "DEA: ", type: "line" },
      { key: "macd_red", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: UP_COLOR }) },
      { key: "macd_green", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: DOWN_COLOR }) },
      {
        key: "macd_cross",
        title: "零轴穿越: ",
        type: "bar",
        baseValue: 0,
        attrs: (p: {
          coordinate?: { current?: Record<string, number> };
          data?: { current?: { macd_cross?: string } };
          barSpace?: { gapBar: number };
        }) => {
          const cross = p.data?.current?.macd_cross;
          if (!cross) return { width: 0 };
          // 加粗：穿越当日柱宽放大到柱距 1.6 倍（常规柱为柱距宽）
          return { width: Math.max(1, (p.barSpace?.gapBar ?? 8) * 1.6) };
        },
        styles: (p: { data?: { current?: { macd_cross?: string } } }) => ({
          color:
            p.data?.current?.macd_cross === "up"
              ? ZTMACD_CROSS_UP
              : ZTMACD_CROSS_DOWN,
        }),
      },
    ],
    calc: (dataList) => {
      // TDX EMA(X,N)：Y=(2X+(N−1)Y')/(N+1) ⟺ ewm(alpha=2/(N+1), adjust=False)，首根取自身
      const closes = dataList.map((k) => {
        const v = (k as unknown as KlineBar).close;
        return Number.isFinite(Number(v)) ? Number(v) : 0;
      });
      const ema = (vals: number[], n: number) => {
        const alpha = 2 / (n + 1);
        const out: number[] = [];
        let prev = vals[0] ?? 0;
        for (const v of vals) {
          prev = v * alpha + prev * (1 - alpha);
          out.push(prev);
        }
        return out;
      };
      const e12 = ema(closes, 12);
      const e26 = ema(closes, 26);
      const dif = e12.map((v, i) => v - e26[i]);
      const dea = ema(dif, 9);
      return dif.map((v, i) => {
        const macd = 2 * (v - dea[i]);
        const prevDif = i > 0 ? dif[i - 1] : 0;
        const cross = i > 0 && prevDif <= 0 && v > 0 ? "up" : i > 0 && prevDif >= 0 && v < 0 ? "down" : "";
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

function toKlineData(bars: KlineBar[]): KLineData[] {
  // M12：amount 不映射 turnover；作为自定义字段随行，供十字光标弹框（成交额）使用
  return bars.map((b) => ({
    timestamp: b.timestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
    amount: b.amount, // 自定义字段：十字光标弹框用（M12 仅约束不映射 turnover）
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
  })) as unknown as KLineData[]; // d.ts: KLineData.open 等为必填 number，可空值仅能经 unknown 断言
}

/** 成交额格式（用户规则）：金额 ≥1 亿元 → x.xx亿元；不足 1 亿 → xx万元（元口径）。
 *  入参为存储值（千元）。 */
function formatAmountKq(kQian: number | null | undefined): string {
  if (kQian == null || !Number.isFinite(Number(kQian))) return "—";
  const yuan = Number(kQian) * 1000;
  const abs = Math.abs(yuan);
  if (abs >= 1e8) return `${(yuan / 1e8).toFixed(2)}亿元`;
  if (abs >= 1e4) return `${(yuan / 1e4).toFixed(0)}万元`;
  return `${yuan.toFixed(0)}元`;
}

/** 成交量大数格式化：≥1亿手 → 亿；≥1千万手 → 千万；≥1万手 → 万。 */
function formatVolumeHand(value: string | number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (abs >= 1e7) return `${(n / 1e7).toFixed(2)}千万`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return `${n}`;
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
    { title: { text: "涨跌幅", color: pctColor ?? NEUTRAL }, value: { text: pctText, color: pctColor ?? NEUTRAL } },
    { title: { text: "成交量(手)", color: NEUTRAL }, value: { text: formatVolumeHand(cur.volume ?? 0), color: NEUTRAL } },
    { title: { text: "成交额", color: NEUTRAL }, value: { text: formatAmountKq(cur.amount ?? null), color: NEUTRAL } },
  ];
}

function fmtPrice(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(2);
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
  /** VOL 之外的副图指标（用户公式 3 个 + 内置扩展，可加可删）。 */
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
    ensureAllRegistered();
    const chart = init(container, { timezone: "Asia/Shanghai" }); // M11
    if (!chart) return;
    chartRef.current = chart;

    // 涨红跌绿（N12）；#4：十字光标跟随弹框（follow_cross + rect）——指到才显示；
    // #5：成交额亿元/万元、成交量万手；坐标轴 decimalFold
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
      formatter: { formatBigNumber: formatVolumeHand },
    });
    chart.setDecimalFold({ threshold: 10000, format: formatVolumeHand }); // #5：坐标轴折叠

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

function ensureAllRegistered() {
  ensureZxRegistered();
  ensureBrickRegistered();
  ensureXpsdRegistered();
  ensureZtMacdRegistered();
}

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

const BRICK_COLORS: Record<string, string> = {
  red: UP_COLOR,
  green: DOWN_COLOR,
  orange: "#ff8000",
};

/** 砖形图 MT（用户 TDX 原文 STICKLINE 着色）：后端派生 mt/mt_prev/mt_color，
 *  前端 rect figure 用 attrs 覆写 y/height 画「前值→现值」楼梯砖；
 *  颜色：红=上行、绿=下行、橙=回踩后再上行且力度不弱（着色优先级最高）。 */
function ensureBrickRegistered() {
  if (brickRegistered) return;
  registerIndicator({
    name: "MTBRICK",
    shortName: "砖形图MT",
    precision: 2,
    figures: [
      {
        key: "mt",
        title: "MT: ",
        type: "rect",
        baseValue: 0,
        attrs: (p: {
          coordinate?: { current?: Record<string, number>; prev?: Record<string, number> };
          barSpace?: { gapBar: number };
        }) => {
          const cur = p.coordinate?.current?.mt;
          const prev = p.coordinate?.prev?.mt;
          if (!Number.isFinite(cur) || !Number.isFinite(prev)) return { width: 0 };
          return {
            y: Math.min(cur as number, prev as number),
            height: Math.max(1, Math.abs((cur as number) - (prev as number))),
            width: Math.max(1, (p.barSpace?.gapBar ?? 8) * 0.9),
          };
        },
        styles: (p: { data?: { current?: { mt_color?: string } } }) => ({
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

/** 知行洗盘线（用户 TDX 原文）：只输出 短期(n1)/长期(n2) 两根线；
 *  中期/中长期仅参与信号计算（后端列仍在，但前端不画）；
 *  四类买入信号柱（−30，堆 0 轴下方）：四线归零(蓝)/线下20(青)/穿红线(绿)/穿黄线(橙)。 */
function ensureXpsdRegistered() {
  if (xpsdRegistered) return;
  registerIndicator({
    name: "XPSD",
    shortName: "知行洗盘线",
    precision: 2,
    figures: [
      { key: "xpsd_short", title: "短期: ", type: "line", styles: () => ({ color: "#00c0c0" }) },
      { key: "xpsd_long", title: "长期: ", type: "line", styles: () => ({ color: "#ef232a" }) },
      { key: "xpsig_zero", title: "四线归零买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#0000ff" }) },
      { key: "xpsig_w20", title: "线下20买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ffff" }) },
      { key: "xpsig_xlong", title: "穿红线买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#00ff00" }) },
      { key: "xpsig_xmid", title: "穿黄线买: ", type: "bar", baseValue: 0, styles: () => ({ color: "#ff9150" }) },
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

const ZTMACD_CROSS_UP = "#2196f3"; // DIFF 上穿零轴：蓝（用户 TDX COLOR0000FF）
const ZTMACD_CROSS_DOWN = "#8bc34a"; // DIFF 下穿零轴：浅绿（COLORLIGREEN）

/** 知行MACD（用户 TDX 原文）：DIFF=EMA(C,12)−EMA(C,26)；DEA=EMA(DIFF,9)；
 *  MACD=2×(DIFF−DEA) COLORSTICK；DIFF 零轴穿越当日柱子加粗高亮（v10 bar figure
 *  支持 attrs.width 覆写，源码 barWidth = attrs.width ?? halfGapBar×2）。 */
function ensureZtMacdRegistered() {
  if (ztMacdRegistered) return;
  registerIndicator({
    name: "ZTMACD",
    shortName: "知行MACD",
    precision: 3,
    figures: [
      { key: "dif", title: "DIF: ", type: "line" },
      { key: "dea", title: "DEA: ", type: "line" },
      { key: "macd_red", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: UP_COLOR }) },
      { key: "macd_green", title: "MACD: ", type: "bar", baseValue: 0, styles: () => ({ color: DOWN_COLOR }) },
      {
        key: "macd_cross",
        title: "零轴穿越: ",
        type: "bar",
        baseValue: 0,
        attrs: (p: {
          coordinate?: { current?: Record<string, number> };
          data?: { current?: { macd_cross?: string } };
          barSpace?: { gapBar: number };
        }) => {
          const cross = p.data?.current?.macd_cross;
          if (!cross) return { width: 0 };
          // 加粗：穿越当日柱宽放大到柱距 1.6 倍（常规柱为柱距宽）
          return { width: Math.max(1, (p.barSpace?.gapBar ?? 8) * 1.6) };
        },
        styles: (p: { data?: { current?: { macd_cross?: string } } }) => ({
          color:
            p.data?.current?.macd_cross === "up"
              ? ZTMACD_CROSS_UP
              : ZTMACD_CROSS_DOWN,
        }),
      },
    ],
    calc: (dataList) => {
      // TDX EMA(X,N)：Y=(2X+(N−1)Y')/(N+1) ⟺ ewm(alpha=2/(N+1), adjust=False)，首根取自身
      const closes = dataList.map((k) => {
        const v = (k as unknown as KlineBar).close;
        return Number.isFinite(Number(v)) ? Number(v) : 0;
      });
      const ema = (vals: number[], n: number) => {
        const alpha = 2 / (n + 1);
        const out: number[] = [];
        let prev = vals[0] ?? 0;
        for (const v of vals) {
          prev = v * alpha + prev * (1 - alpha);
          out.push(prev);
        }
        return out;
      };
      const e12 = ema(closes, 12);
      const e26 = ema(closes, 26);
      const dif = e12.map((v, i) => v - e26[i]);
      const dea = ema(dif, 9);
      return dif.map((v, i) => {
        const macd = 2 * (v - dea[i]);
        const prevDif = i > 0 ? dif[i - 1] : 0;
        const cross = i > 0 && prevDif <= 0 && v > 0 ? "up" : i > 0 && prevDif >= 0 && v < 0 ? "down" : "";
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
