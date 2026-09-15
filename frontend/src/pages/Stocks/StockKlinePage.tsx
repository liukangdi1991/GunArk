import { ArrowLeftOutlined, DownOutlined } from "@ant-design/icons";
import { Alert, Button, Dropdown, Result, Segmented, Space, Spin, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { formatNumber } from "../../utils/format";
import type { BacktestMark } from "../../types/backtest";
import type { AdjustMode, KlineBar, KlinePeriod, StockSnapshot } from "../../types/kline";
import { getBacktestMarks } from "../../services/backtests";
import { getStockSnapshot } from "../../services/marketData";
import { attachMarks, countMarks } from "../../utils/backtestMarks";
import { currentSearchParams, replaceSearchParams } from "../../utils/urlState";
import { KlineChart } from "./KlineChart";
import { useKline } from "./useKline";

const { Title, Text } = Typography;

const PERIOD_OPTIONS: { value: KlinePeriod; label: string }[] = [
  { value: "daily", label: "日" },
  { value: "weekly", label: "周" },
  { value: "monthly", label: "月" },
];
const ADJUST_OPTIONS: { value: AdjustMode; label: string }[] = [
  { value: "qfq", label: "前复权" },
  { value: "none", label: "不复权" },
];
const MAIN_OVERLAY_OPTIONS: { value: string; label: string }[] = [
  { value: "MA", label: "均线 MA(34/55/144/233)" },
  { value: "ZX", label: "多空线/短期趋势线" },
];
const EXTRA_SUB_INDICATORS: { value: string; label: string }[] = [
  { value: "ZTMACD", label: "知行MACD" },
  { value: "KDJ", label: "KDJ" },
  { value: "RSI", label: "RSI" },
  { value: "BOLL", label: "BOLL" },
  { value: "WR", label: "WR" },
  { value: "BBI", label: "BBI" },
  { value: "MTBRICK", label: "砖形图(MT)" },
  { value: "XPSD", label: "知行洗盘线" },
];

const PREF_KEY = "kline.indicators.v1";

interface IndicatorPrefs {
  mainOverlays?: string[];
  subIndicators?: string[];
}

/** 副图偏好白名单归一：内置 MACD 迁移为知行MACD（2026-09-09 换接），未知项丢弃。 */
function loadIndicatorPrefs(): IndicatorPrefs {
  const subValues = new Set(EXTRA_SUB_INDICATORS.map((o) => o.value));
  try {
    const saved = JSON.parse(localStorage.getItem(PREF_KEY) ?? "{}") as IndicatorPrefs;
    return {
      mainOverlays: Array.isArray(saved.mainOverlays) ? saved.mainOverlays : ["MA", "ZX"],
      subIndicators: Array.isArray(saved.subIndicators)
        ? saved.subIndicators
            .map((v) => (v === "MACD" ? "ZTMACD" : v))
            .filter((v): v is string => typeof v === "string" && subValues.has(v))
        : [],
    };
  } catch {
    return { mainOverlays: ["MA", "ZX"], subIndicators: [] };
  }
}

/** §4.3.4：qfq 档用序列比值（因子比相消，与真实涨幅等价）；
 *  none 档优先 (close−pre_close)/pre_close（交易所口径），null 回退序列比。 */
function pctChange(bars: KlineBar[], adjust: AdjustMode): number | null {
  if (bars.length < 2) return null;
  const last = bars[bars.length - 1];
  if (last.close == null) return null;
  if (adjust === "none" && last.pre_close != null && last.pre_close > 0) {
    return (last.close - last.pre_close) / last.pre_close;
  }
  const prev = bars[bars.length - 2];
  if (prev.close == null || prev.close <= 0) return null;
  return (last.close - prev.close) / prev.close;
}

export default function StockKlinePage() {
  const navigate = useNavigate();
  const { code = "" } = useParams();
  // URL 只当挂载初值 + 单向镜像：写入走 replaceState（不经路由导航），否则每次切周期/复权
  // 都会重挂载本页——图表重建、数据重取，页面还被打回顶部。
  const initialParams = useRef(currentSearchParams());
  const initialPrefs = useMemo(loadIndicatorPrefs, []);
  const [mainOverlays, setMainOverlays] = useState<string[]>(initialPrefs.mainOverlays ?? ["MA", "ZX"]);
  const [subIndicators, setSubIndicators] = useState<string[]>(initialPrefs.subIndicators ?? []);
  const [retryKey, setRetryKey] = useState(0);
  const [snapshot, setSnapshot] = useState<StockSnapshot | null>(null);

  // —— 回测买卖标记（spec M1/M7）：只在 URL 带合法 bt 时拉一次；失败只影响提示条 ——
  const btKey = useMemo(() => {
    const raw = initialParams.current.get("bt");
    return raw && /^[A-Za-z0-9_]{1,128}$/.test(raw) ? raw : null;
  }, []);
  const [marks, setMarks] = useState<BacktestMark[] | null>(null);
  const [marksRev, setMarksRev] = useState(0);
  const [marksLoading, setMarksLoading] = useState(Boolean(btKey));
  const [marksError, setMarksError] = useState("");
  const [btCleared, setBtCleared] = useState(false);

  const normalizePeriod = (raw: string | null): KlinePeriod =>
    raw === "weekly" || raw === "monthly" ? raw : "daily"; // 白名单归一（N10）
  const normalizeAdjust = (raw: string | null): AdjustMode => (raw === "none" ? "none" : "qfq");
  const [period, setPeriod] = useState<KlinePeriod>(() => normalizePeriod(initialParams.current.get("period")));
  const [adjust, setAdjust] = useState<AdjustMode>(() => normalizeAdjust(initialParams.current.get("adjust")));
  const codeValid = /^\d{6}$/.test(code);

  // 挂载时把 URL 里的脏参数归一化写回（本来干净的 URL 不动）
  useEffect(() => {
    const raw = initialParams.current;
    if (!raw.has("period") && !raw.has("adjust")) return;
    replaceSearchParams((params) => {
      if (params.get("period") === period && params.get("adjust") === adjust) return false;
      params.set("period", period);
      params.set("adjust", adjust);
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { payload, loading, error, status } = useKline(code, period, adjust, retryKey);

  useEffect(() => {
    if (!btKey || !codeValid || btCleared) {
      setMarksLoading(false);
      return;
    }
    const controller = new AbortController();
    setMarksLoading(true);
    getBacktestMarks(btKey, code, controller.signal)
      .then((resp) => {
        setMarks(resp.items);
        setMarksRev((n) => n + 1);
        setMarksError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return; // 静默（切股已接管）
        setMarks([]);
        setMarksRev((n) => n + 1);
        setMarksError(err instanceof Error ? err.message : "加载回测标记失败。");
      })
      .finally(() => setMarksLoading(false));
    return () => controller.abort();
  }, [btKey, code, codeValid, btCleared]);

  // 标记按日期归并到 bar（纯函数，spec §5.3）；无标记时原样透传
  const payloadWithMarks = useMemo(
    () => (payload && marks && marks.length > 0 ? { ...payload, bars: attachMarks(payload.bars, marks) } : payload),
    [payload, marks],
  );
  const markCounts = useMemo(() => countMarks(marks ?? []), [marks]);
  const clearBt = useCallback(() => {
    setBtCleared(true);
    setMarks(null);
    setMarksRev((n) => n + 1);
    replaceSearchParams((params) => {
      const had = params.has("bt");
      params.delete("bt");
      return had;
    });
  }, []);
  const pct = useMemo(
    () => (payload ? pctChange(payload.bars, adjust) : null),
    [payload, adjust],
  );

  // #1 个性化持久化（localStorage，单用户本地工具）：指标选择跨会话记忆
  useEffect(() => {
    localStorage.setItem(
      PREF_KEY,
      JSON.stringify({ mainOverlays, subIndicators } satisfies IndicatorPrefs),
    );
  }, [mainOverlays, subIndicators]);

  // #6 个股快照（流通市值/换手率等；无 token/异常时字段为 null，界面显示「—」）
  useEffect(() => {
    if (!codeValid) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    getStockSnapshot(code)
      .then((snap) => {
        if (!cancelled) setSnapshot(snap);
      })
      .catch(() => {
        if (!cancelled) setSnapshot(null);
      });
    return () => {
      cancelled = true;
    };
  }, [codeValid, code]);

  const setParam = (key: "period" | "adjust", value: string) => {
    const nextPeriod = key === "period" ? normalizePeriod(value) : period;
    const nextAdjust = key === "adjust" ? normalizeAdjust(value) : adjust;
    setPeriod(nextPeriod);
    setAdjust(nextAdjust);
    // N18：只改地址栏、不刷历史栈，也不触发路由重挂载；回退由浏览器驱动
    replaceSearchParams((params) => {
      if (params.get("period") === nextPeriod && params.get("adjust") === nextAdjust) return false;
      params.set("period", nextPeriod);
      params.set("adjust", nextAdjust);
      return true;
    });
  };

  const toggleOverlay = (key: string) => {
    setMainOverlays((cur) =>
      cur.includes(key) ? cur.filter((n) => n !== key) : [...cur, key],
    );
  };
  const toggleSub = (key: string) => {
    setSubIndicators((cur) =>
      cur.includes(key) ? cur.filter((n) => n !== key) : [...cur, key],
    );
  };

  if (!codeValid) {
    return (
      <Result
        status="error"
        title="非法的股票代码"
        subTitle={`「${code}」不是 6 位数字代码。`}
        extra={<Button onClick={() => navigate(-1)}>返回</Button>}
      />
    );
  }

  if (status === 404) {
    return (
      <Result
        status="404"
        title="无该股行情数据"
        subTitle={`${code} 在本地行情库中没有数据（未同步或已退市出库）。`}
        extra={<Button onClick={() => navigate(-1)}>返回</Button>}
      />
    );
  }

  const lastBar = payload?.bars.length ? payload.bars[payload.bars.length - 1] : null;
  const up = pct != null && pct >= 0;
  const priceColor = pct == null ? "rgba(0,0,0,0.45)" : up ? "#ef232a" : "#14b143";
  const circYi =
    snapshot?.circ_mv != null ? (snapshot.circ_mv / 1e4).toFixed(0) : null; // 万元 → 亿元

  return (
    <div className="stock-kline-page">
      <Space align="center" split={<span>·</span>} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} />
        <Title level={4} style={{ margin: 0 }}>
          {payload ? `${payload.name} ${payload.code}` : code}
        </Title>
        <Text type="secondary">{payload?.industry ?? "-"}</Text>
        {lastBar?.close != null && (
          <Text style={{ color: priceColor, fontSize: 18 }}>
            {formatNumber(lastBar.close, 2)}
          </Text>
        )}
        {pct != null && (
          <Text style={{ color: up ? "#ef232a" : "#14b143" }}>
            {up ? "+" : ""}
            {(pct * 100).toFixed(2)}%
          </Text>
        )}
        {pct == null && <Text type="secondary">—</Text>}
        <Text type="secondary">数据截至 {payload?.last_bar_date ?? "-"}</Text>
        {circYi != null && <Text type="secondary">流通市值 {circYi}亿</Text>}
      </Space>

      {payload?.adjust_degraded && (
        <Alert
          type="warning"
          showIcon
          message="前复权因子未通过真实性校验，当前退化为原价（与不复权逐值相同）"
          description="可能原因：① 行情库未完成 2015 基线重建（请在「行情数据」页执行全量同步后刷新）；② 数据源复权因子与交易所昨收在个别日期矛盾（如回购注销等非除权股本事件被计入因子，孤立数行误差可达 6%~22%）——后者属数据源问题，原价图不受影响。"
        />
      )}

      {btKey && !btCleared && (
        <Alert
          type={marksError ? "warning" : "info"}
          showIcon
          message={
            marksLoading
              ? "正在加载回测标记…"
              : marksError
                ? `回测标记加载失败：${marksError}`
                : markCounts.buy + markCounts.sell === 0
                  ? "该回测在这只票上没有成交"
                  : `正在显示回测 ${btKey} 的买卖标记（B ${markCounts.buy} / S ${markCounts.sell}）`
          }
          action={
            <Space>
              <Link to={`/backtests/${encodeURIComponent(btKey)}`}>查看报告</Link>
              <Button type="link" size="small" onClick={clearBt}>
                清除
              </Button>
            </Space>
          }
        />
      )}

      <Space wrap>
        <Segmented
          options={PERIOD_OPTIONS}
          value={period}
          disabled={loading}
          onChange={(value) => setParam("period", value as string)}
        />
        <Segmented
          options={ADJUST_OPTIONS}
          value={adjust}
          disabled={loading}
          onChange={(value) => setParam("adjust", value as string)}
        />
        <Dropdown
          disabled={loading}
          menu={{
            items: MAIN_OVERLAY_OPTIONS.map((opt) => ({
              key: opt.value,
              label: (mainOverlays.includes(opt.value) ? "✓ " : "") + opt.label,
            })),
            onClick: ({ key }) => toggleOverlay(key),
          }}
        >
          <Button>
            主图指标 <DownOutlined />
          </Button>
        </Dropdown>
        <Dropdown
          disabled={loading}
          menu={{
            items: EXTRA_SUB_INDICATORS.map((opt) => ({
              key: opt.value,
              label: (subIndicators.includes(opt.value) ? "✓ " : "") + opt.label,
            })),
            onClick: ({ key }) => toggleSub(key),
          }}
        >
          <Button>
            副图指标 <DownOutlined />
          </Button>
        </Dropdown>
      </Space>

      {loading && (
        <div style={{ textAlign: "center", padding: 80 }}>
          <Spin tip="加载K线数据…" />
        </div>
      )}

      {!loading && error && status !== 404 && (
        <Result
          status="error"
          title={status === 503 ? "行情数据正在更新，请稍后重试" : "K线数据加载失败"}
          subTitle={error}
          extra={<Button type="primary" onClick={() => setRetryKey((k) => k + 1)}>重试</Button>}
        />
      )}

      {!loading && !error && payloadWithMarks && (
        <KlineChart
          key={`${payloadWithMarks.code}-${payloadWithMarks.period}-${payloadWithMarks.adjust}-${marksRev}`}
          payload={payloadWithMarks}
          mainOverlays={mainOverlays}
          subIndicators={subIndicators}
        />
      )}
    </div>
  );
}
