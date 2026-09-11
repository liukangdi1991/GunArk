import { ArrowLeftOutlined, DownOutlined } from "@ant-design/icons";
import { Alert, Button, Dropdown, Result, Segmented, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { formatNumber } from "../../utils/format";
import type { AdjustMode, KlineBar, KlinePeriod, StockSnapshot } from "../../types/kline";
import { getStockSnapshot } from "../../services/marketData";
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
  const [searchParams, setSearchParams] = useSearchParams();
  const initialPrefs = useMemo(loadIndicatorPrefs, []);
  const [mainOverlays, setMainOverlays] = useState<string[]>(initialPrefs.mainOverlays ?? ["MA", "ZX"]);
  const [subIndicators, setSubIndicators] = useState<string[]>(initialPrefs.subIndicators ?? []);
  const [retryKey, setRetryKey] = useState(0);
  const [snapshot, setSnapshot] = useState<StockSnapshot | null>(null);

  const rawPeriod = searchParams.get("period");
  const rawAdjust = searchParams.get("adjust");
  const period: KlinePeriod =
    rawPeriod === "weekly" || rawPeriod === "monthly" ? rawPeriod : "daily"; // 白名单归一（N10）
  const adjust: AdjustMode = rawAdjust === "none" ? "none" : "qfq";
  const codeValid = /^\d{6}$/.test(code);

  // 归一化后回写 URL（replace），后续刷新/分享不再带脏参数
  useEffect(() => {
    if (rawPeriod == null && rawAdjust == null) return;
    if (rawPeriod === period && rawAdjust === adjust) return;
    const next = new URLSearchParams(searchParams);
    next.set("period", period);
    next.set("adjust", adjust);
    setSearchParams(next, { replace: true });
  }, [rawPeriod, rawAdjust, period, adjust, searchParams, setSearchParams]);

  const { payload, loading, error, status } = useKline(code, period, adjust, retryKey);
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
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true }); // N18：replace 不刷历史栈，回退由浏览器驱动
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
          message="复权因子缺失，前复权当前退化为原价（与不复权逐值相同）"
          description="本地行情库尚未完成 2015 基线重建，请在「行情数据」页执行全量同步后刷新。"
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

      {!loading && !error && payload && (
        <KlineChart payload={payload} mainOverlays={mainOverlays} subIndicators={subIndicators} />
      )}
    </div>
  );
}
