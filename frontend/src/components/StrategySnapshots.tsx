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
    lines.push(`长期多空线与短期趋势线的相对距离小于 ${formatRatio(numberParam(params, "zx_stick_limit_threshold"))}。`);
    lines.push(`选股当日成交量是最近 ${numberParam(params, "recent_volume_window") || "-"} 个交易日新低。`);
    lines.push(`收盘价不低于长期多空线的 ${formatRatio(numberParam(params, "close_vs_long_term_bull_bear_line_limit_threshold"))}。`);
    return lines;
  }

  if (strategy.class === "PerfectB1Selector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push(`当日振幅小于 ${formatRatio(numberParam(params, "amplitude_limit"))}。`);
    lines.push(
      `当日涨跌幅在 ${formatSignedRatio(numberParam(params, "pct_chg_lower"))} ~ ${formatSignedRatio(numberParam(params, "pct_chg_upper"))} 之间。`,
    );
    lines.push(`收盘价高于 ${numberParam(params, "ma_window") || "-"} 日均线。`);
    lines.push("收盘价高于长期多空线。");
    lines.push(
      `最近 ${numberParam(params, "volume_spike_lookback") || "-"} 个交易日内存在上涨倍量柱，成交量至少为前一日的 ${numberParam(params, "volume_spike_multiple") ?? "-"} 倍。`,
    );
    lines.push("短期趋势线高于长期多空线。");
    if (numberParam(params, "volume_step_down_window") !== null) {
      lines.push(
        `最近 ${numberParam(params, "volume_step_down_window")} 个交易日内，至少 ${numberParam(params, "min_volume_step_down_days")} 个交易日成交量低于前一日。`,
      );
    }
    if (numberParam(params, "recent_volume_new_low_window") !== null) {
      lines.push(`选股当日成交量是最近 ${numberParam(params, "recent_volume_new_low_window")} 个交易日新低。`);
    }
    return lines;
  }

  if (strategy.class === "BBIKDJSelector") {
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    lines.push(`BBI 至少观察 ${numberParam(params, "bbi_min_window") ?? "-"} 个交易日，并满足上行过滤。`);
    lines.push(`价格波动范围限制在 ${formatPercentLiteral(numberParam(params, "price_range_pct"))} 内。`);
    lines.push(`BBI 位置分位低于 ${formatRatio(numberParam(params, "bbi_q_threshold"))}，J 值分位低于 ${formatRatio(numberParam(params, "j_q_threshold"))}。`);
    return lines;
  }

  if (strategy.class === "SuperB1Selector") {
    lines.push(`最近 ${numberParam(params, "lookback_n") ?? "-"} 个交易日内识别短线反转结构。`);
    lines.push(`收盘缩量阈值为 ${formatRatio(numberParam(params, "close_vol_pct"))}，价格回撤阈值为 ${formatRatio(numberParam(params, "price_drop_pct"))}。`);
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}，并叠加 B1 基础条件。`);
    return lines;
  }

  if (strategy.class === "BBIShortLongSelector") {
    lines.push(`短周期 RSV 使用 ${numberParam(params, "n_short") ?? "-"} 日，长周期 RSV 使用 ${numberParam(params, "n_long") ?? "-"} 日。`);
    lines.push(`上沿 RSV 阈值为 ${numberParam(params, "upper_rsv_threshold") ?? "-"}，下沿 RSV 阈值为 ${numberParam(params, "lower_rsv_threshold") ?? "-"}。`);
    lines.push(`BBI 至少观察 ${numberParam(params, "bbi_min_window") ?? "-"} 个交易日，并满足斜率过滤。`);
    return lines;
  }

  if (strategy.class === "PeakKDJSelector") {
    lines.push(`先识别最近 ${numberParam(params, "max_window") ?? "-"} 个交易日的峰值回撤结构。`);
    lines.push(`波动阈值为 ${formatRatio(numberParam(params, "fluc_threshold"))}，缺口阈值为 ${formatRatio(numberParam(params, "gap_threshold"))}。`);
    lines.push(`KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}，J 值分位低于 ${formatRatio(numberParam(params, "j_q_threshold"))}。`);
    return lines;
  }

  if (strategy.class === "MA60CrossVolumeWaveSelector") {
    lines.push(`最近 ${numberParam(params, "lookback_n") ?? "-"} 个交易日内出现有效上穿 60 日均线。`);
    lines.push(`成交量至少放大到前一日的 ${numberParam(params, "vol_multiple") ?? "-"} 倍。`);
    lines.push(`60 日均线斜率观察 ${numberParam(params, "ma60_slope_days") ?? "-"} 个交易日，KDJ 的 J 值小于 ${numberParam(params, "j_threshold") ?? "-"}。`);
    return lines;
  }

  if (strategy.class === "BigBullishVolumeSelector") {
    lines.push(`当日涨幅至少达到 ${formatRatio(numberParam(params, "up_pct_threshold"))}，并要求阳线收盘。`);
    lines.push(`上影线比例不超过 ${formatRatio(numberParam(params, "upper_wick_pct_max"))}。`);
    lines.push(`成交量观察 ${numberParam(params, "vol_lookback_n") ?? "-"} 个交易日，放量倍数至少为 ${numberParam(params, "vol_multiple") ?? "-"} 倍。`);
    lines.push(`收盘价不高于短期趋势线的 ${numberParam(params, "close_lt_short_term_trend_line_mult") ?? "-"} 倍，避免明显追高。`);
    return lines;
  }

  if (strategy.class === "VolumeSpikeBalanceSelector") {
    lines.push(
      `近 ${numberParam(params, "volume_spike_lookback") ?? "-"} 个交易日中存在上涨倍量柱，成交量大于前一日的 ${numberParam(params, "volume_spike_multiple") ?? "-"} 倍。`,
    );
    lines.push(`倍量柱到选股日需大于 ${numberParam(params, "min_spike_elapsed_days") ?? "-"} 个交易日。`);
    lines.push("选股当日成交量是倍量柱到选股日以来最低。");
    lines.push(
      `最近 ${numberParam(params, "zx_stick_window") ?? "-"} 个交易日每天两线黏合，相对距离小于 ${formatRatio(numberParam(params, "zx_stick_limit_threshold"))}。`,
    );
    lines.push("选股当日收盘价低于长期多空线。");
    return lines;
  }

  return fallbackLines.length ? fallbackLines : ["未配置策略说明，本次报告已保存运行时策略快照。"];
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
