import { Card, Space, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import type { Strategy } from "../types/strategy";
import { capitalModeLabel } from "../utils/capital";
import { tradeStrategyLabel } from "../utils/tradeStrategy";

const { Text } = Typography;

function numberParam(params: Record<string, unknown>, key: string): number | null {
  const value = Number(params[key]);
  return Number.isFinite(value) ? value : null;
}

function formatRatio(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${(value * 100).toFixed(2)}%`;
}

function formatSignedRatio(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${formatRatio(value)}`;
}

function formatPercentLiteral(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${value.toFixed(2)}%`;
}

function formatMoney(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return num.toLocaleString("zh-CN", {
    maximumFractionDigits: 0,
  });
}

function describeTradeRule(
  params: Record<string, unknown>,
  capitalMode?: string,
  cashPerTrade?: number,
): string[] {
  const holdDays = numberParam(params, "hold_n_days");
  const tradeStrategy = typeof params.trade_strategy === "string" ? params.trade_strategy : "";
  const tradeStrategyName = typeof params.trade_strategy_name === "string" && params.trade_strategy_name
    ? params.trade_strategy_name
    : tradeStrategyLabel(tradeStrategy);
  const recentLowWindow = numberParam(params, "close_below_recent_low_stop_window");
  const lines = [
    "选股日为 T 日，T+1 按开盘价买入，并计入买入滑点和交易费用。",
    holdDays === null
      ? "卖出日按当前交易规则执行，并计入卖出滑点和交易费用。"
      : `默认持仓 ${holdDays} 个交易日，T+${holdDays + 1} 按收盘价卖出，并计入卖出滑点和交易费用。`,
  ];
  if (tradeStrategyName !== "-") {
    lines.unshift(`交易策略为${tradeStrategyName}。`);
  }

  if (params["连续两日收盘低于长期多空线强制卖出"]) {
    lines.push("持仓期间若连续两日收盘价低于长期多空线，则在第二日按收盘价触发强制卖出。");
  }
  if (recentLowWindow !== null) {
    lines.push(`持仓期间若今日收盘价低于买入后截至昨日最近 ${recentLowWindow} 个交易日最低价，则按今日收盘价触发止损卖出。`);
  }
  lines.push("如果卖出日跌停无法成交，跌停顺延卖出优先于其他卖出规则。");

  if (capitalMode === "unlimited_cash") {
    lines.push(`资金模式为${capitalModeLabel(capitalMode)}：每只股票按 ${formatMoney(cashPerTrade)} 元名义金额买入，不做现金不足限制。`);
  } else if (capitalMode) {
    lines.push(`资金模式为${capitalModeLabel(capitalMode)}：买入前会校验可用现金。`);
  }

  return lines;
}

function splitDescription(description: string): string[] {
  return description
    .split(/[；;]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => (/[。.!！?？]$/.test(item) ? item : `${item}。`));
}

function describeStrategy(strategy: Strategy): string[] {
  const params = strategy.params || {};
  const fallbackLines = strategy.description ? splitDescription(strategy.description) : [];
  const lines: string[] = [];

  if (strategy.class === "ZXDKXBalanceSelector") {
    lines.push(`收盘价不超过长期多空线的 ${formatRatio(numberParam(params, "close_vs_long_term_bull_bear_line_limit_threshold"))}。`);
    lines.push(`近 10 个交易日短期趋势线与长期多空线粘合（偏离率最小值低于 ${formatRatio(numberParam(params, "zx_stick_limit_threshold"))}）。`);
    return lines;
  }

  if (strategy.class === "PerfectB1Selector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push(`当日振幅不超过 ${formatRatio(numberParam(params, "amplitude_limit"))}。`);
    lines.push(`当日涨跌幅在 ${formatSignedRatio(numberParam(params, "pct_chg_lower"))} ~ ${formatSignedRatio(numberParam(params, "pct_chg_upper"))} 之间。`);
    return lines;
  }

  if (strategy.class === "BBIKDJSelector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push("DIF（EMA12-EMA26）大于 0。");
    lines.push(`BBI 上行：至少观察 ${numberParam(params, "bbi_min_window") ?? "-"} 个交易日，分位阈值 ${formatRatio(numberParam(params, "bbi_q_threshold"))}。`);
    return lines;
  }

  if (strategy.class === "SuperB1Selector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push(`当日涨跌幅不超过 ${formatRatio(numberParam(params, "price_drop_pct"))}（避免追高）。`);
    const closeVolPct = numberParam(params, "close_vol_pct");
    lines.push(
      `成交量不低于最近 ${numberParam(params, "lookback_n") ?? "-"} 个交易日日均量的 ${closeVolPct === null ? "-" : formatRatio(1 - closeVolPct)}。`,
    );
    return lines;
  }

  if (strategy.class === "BBIShortLongSelector") {
    lines.push(`短期均线（${numberParam(params, "n_short") ?? "-"} 日）高于长期均线（${numberParam(params, "n_long") ?? "-"} 日）。`);
    lines.push(`BBI 上行：至少观察 ${numberParam(params, "bbi_min_window") ?? "-"} 个交易日。`);
    return lines;
  }

  if (strategy.class === "PeakKDJSelector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push(`近 ${numberParam(params, "max_window") ?? "-"} 个交易日区间波动（高低点差/高点）不小于 ${formatRatio(numberParam(params, "fluc_threshold"))}。`);
    lines.push(`收盘价较区间高点回撤不小于 ${formatRatio(numberParam(params, "gap_threshold"))}。`);
    return lines;
  }

  if (strategy.class === "MA60CrossVolumeWaveSelector") {
    lines.push("收盘价站上 60 日均线。");
    lines.push(`成交量不低于最近 ${numberParam(params, "lookback_n") ?? "-"} 个交易日日均量的 ${numberParam(params, "vol_multiple") ?? "-"} 倍。`);
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    return lines;
  }

  if (strategy.class === "BigBullishVolumeSelector") {
    lines.push(`当日涨幅（收盘相对开盘）至少达到 ${formatRatio(numberParam(params, "up_pct_threshold"))}。`);
    lines.push(`上影线比例不超过 ${formatRatio(numberParam(params, "upper_wick_pct_max"))}。`);
    lines.push(`成交量不低于前一日的 ${numberParam(params, "vol_multiple") ?? "-"} 倍。`);
    return lines;
  }

  if (strategy.class === "VolumeSpikeBalanceSelector") {
    lines.push(`近 ${numberParam(params, "volume_spike_lookback") ?? "-"} 个交易日内存在上涨倍量柱（成交量大于前一日 ${numberParam(params, "volume_spike_multiple") ?? "-"} 倍且收阳）。`);
    lines.push(`距倍量柱至少 ${numberParam(params, "min_spike_elapsed_days") ?? "-"} 个交易日。`);
    lines.push("选股当日成交量不超过倍量柱之后的最低成交量，且收盘价低于长期多空线。");
    return lines;
  }

  return fallbackLines.length ? fallbackLines : ["未配置策略说明，本次报告已保存运行时策略快照。"];
  if (strategy.class === "SingleNeedleDown20Selector") {
    lines.push(`${numberParam(params, "n1") ?? "-"} 日随机指标 ≤ ${numberParam(params, "short_max") ?? "-"}。`);
    lines.push(`${numberParam(params, "n2") ?? "-"} 日区间随机指标 > ${numberParam(params, "long_min") ?? "-"}。`);
    lines.push(`流通市值 ≥ ${numberParam(params, "circ_mv_min_yi") ?? "-"} 亿。`);
    return lines;
  }

  if (strategy.class === "BrickChartSelector") {
    lines.push(`MT 振荡器（${numberParam(params, "n") ?? "-"} 日窗口）今日转升，红柱高度 ≥ 昨日绿柱高度。`);
    lines.push("今日之前连续 3 日绿柱。");
    lines.push("收盘价 ≥ 四线均值（MA14/28/57/114 平均）。");
    return lines;
  }

  if (strategy.class === "OversoldBottomFishingSelector") {
    lines.push("MT 振荡器转升且前 3 日绿柱（红柱高度 ≥ 昨日绿柱）。");
    lines.push(`收盘二阶差分 > 0 且为近 ${numberParam(params, "dd2_window") ?? "-"} 日最高（企稳拐点）。`);
    lines.push("收盘 < ZXK < DKK（超跌区域，ZXK 为 EMA10 双平滑）。");
    lines.push(`MACD DIF 近 ${numberParam(params, "every_neg") ?? "-"} 日为负、今日走平/回升，前 ${numberParam(params, "every_down") ?? "-"} 日持续下行。`);
    return lines;
  }

  if (strategy.class === "UltimateBrickChartSelector") {
    lines.push("MT 振荡器转升且前 3 日连续绿柱（红柱高度 ≥ 昨日绿柱）。");
    lines.push(`多头排列：收盘 ≥ ZXK > DKK（ZXK 为 EMA${numberParam(params, "ema1") ?? "-"} 双平滑）。`);
    return lines;
  }
}

export function StrategySnapshots({
  strategies,
  extra,
}: {
  strategies: Strategy[];
  extra?: ReactNode;
}) {
  return (
    <Space direction="vertical" size={12} className="condition-sections">
      {extra ? (
        <section className="condition-section">
          <div className="condition-section-title">交易规则</div>
          {extra}
        </section>
      ) : null}
      <section className="condition-section">
        <div className="condition-section-title">选股策略</div>
        <div className="strategy-condition-grid">
          {(strategies || []).map((strategy) => (
            <Card className="strategy-snapshot-card" key={strategy.name}>
              <Space direction="vertical" size={8}>
                <Space wrap>
                  <Tag color="blue">{strategy.name}</Tag>
                </Space>
                <ul className="snapshot-lines">
                  {describeStrategy(strategy).map((line) => (
                    <li key={line}>
                      <Text>{line}</Text>
                    </li>
                  ))}
                </ul>
              </Space>
            </Card>
          ))}
        </div>
      </section>
    </Space>
  );
}

export function ParamsSnapshot({
  params,
  capitalMode,
  cashPerTrade,
}: {
  params: Record<string, unknown>;
  capitalMode?: string;
  cashPerTrade?: number;
}) {
  return (
    <Card className="strategy-snapshot-card">
      <Space direction="vertical" size={8}>
        <ul className="snapshot-lines">
          {describeTradeRule(params, capitalMode, cashPerTrade).map((line) => (
            <li key={line}>
              <Text>{line}</Text>
            </li>
          ))}
        </ul>
      </Space>
    </Card>
  );
}
