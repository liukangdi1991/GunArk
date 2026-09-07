import { Alert, Card, Space, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import type { Strategy } from "../types/strategy";
import { capitalModeLabel } from "../utils/capital";
import { tradeStrategyLabel } from "../utils/tradeStrategy";

const { Text } = Typography;

function numberParam(params: Record<string, unknown>, key: string): number | null {
  const raw = params[key];
  // Number(null) === 0 且有限，会把后端的 null（如 close_below_recent_low_stop_window）
  // 当成 0，渲染出「低于最近 0 个交易日最低价止损」这种假规则。空值一律视为未设。
  if (raw === null || raw === undefined || raw === "") {
    return null;
  }
  const value = Number(raw);
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

function describeCosts(params: Record<string, unknown>): string {
  const costs = (params.costs || {}) as Record<string, unknown>;
  const ratio = (key: string) => {
    const value = Number(costs[key]);
    return Number.isFinite(value) ? formatRatio(value) : "-";
  };
  const basisPoint = (key: string) => {
    // 值本身就是 bp（买卖滑点）
    const value = Number(costs[key]);
    return Number.isFinite(value) ? `${value}bp` : "-";
  };
  const ratioToBasisPoint = (key: string) => {
    // 值是比率，×10000 折成 bp：过户费 0.00001 = 0.1bp，直接当 bp 会差一万倍
    const value = Number(costs[key]);
    return Number.isFinite(value) ? `${+(value * 10000).toFixed(4)}bp` : "-";
  };
  const lotSize = numberParam(params, "lot_size");
  const commissionMin = Number(costs.commission_min);

  return (
    `交易成本：佣金 ${ratio("commission_rate")}` +
    `${Number.isFinite(commissionMin) ? `（单笔最低 ${formatMoney(commissionMin)} 元）` : ""}` +
    `、卖出印花税 ${ratio("stamp_duty_rate_sell")}、过户费 ${ratioToBasisPoint("transfer_fee_rate")}` +
    `、滑点买入 ${basisPoint("slippage_buy_bp")} / 卖出 ${basisPoint("slippage_sell_bp")}` +
    `${lotSize !== null ? `，一手 ${lotSize} 股` : ""}。`
  );
}

function describePositionLimits(
  params: Record<string, unknown>,
  capitalMode?: string,
): string {
  const limits = (params.position_limits || {}) as Record<string, unknown>;
  // 未设（null）与 0 必须分开，且引擎对 0 的处理逐项不同：
  //   max_positions / max_daily_new_positions：0 是硬上限（照 0 执行，一笔都不开）；
  //   target_positions：引擎走真值判断，0 等同未设，不能显示「权益 ÷ 0」；
  //   max_single_position_pct：0 是硬上限（每份预算 0，买不进）。
  const maxPositions = numberParam(limits, "max_positions");
  const maxDaily = numberParam(limits, "max_daily_new_positions");
  const target = numberParam(limits, "target_positions");
  const singlePct = numberParam(limits, "max_single_position_pct");

  // target_positions / max_single_position_pct 只喂 _calc_position_budget（按权益算每份
  // 预算），仅 realistic 生效；unlimited_cash 每笔预算是固定名义额，这两项设了也不起作用，
  // 写进报告就是假话。max_positions / max_daily_new_positions 进开仓槽位逻辑，两种模式都生效。
  const unlimited = capitalMode === "unlimited_cash";

  const parts: string[] = [];
  if (maxPositions !== null && maxPositions >= 0) {
    parts.push(`同时最多持有 ${maxPositions} 份`);
  }
  if (maxDaily !== null && maxDaily >= 0) {
    parts.push(`每天最多新开 ${maxDaily} 份`);
  }
  if (!unlimited) {
    if (target !== null && target > 0) {
      parts.push(`目标持仓 ${target} 份（每份预算 = 权益 ÷ ${target}）`);
    }
    if (singlePct !== null && singlePct >= 0) {
      parts.push(`单份持仓不超过权益的 ${formatRatio(singlePct)}`);
    }
  }
  if (parts.length) {
    return `持仓上限：${parts.join("、")}。`;
  }
  return unlimited
    ? "不限持仓数量与开仓节奏（选出多少买多少）。"
    : "不限持仓数量、开仓节奏与单票占比（选出多少买多少）。";
}

function describeTradeRule(
  params: Record<string, unknown>,
  capitalMode?: string,
  cashPerTrade?: number,
): string[] {
  const holdDays = numberParam(params, "fixed_hold_n_days");
  const tradeStrategy = typeof params.trade_strategy === "string" ? params.trade_strategy : "";
  const tradeStrategyName = typeof params.trade_strategy_name === "string" && params.trade_strategy_name
    ? params.trade_strategy_name
    : tradeStrategyLabel(tradeStrategy);
  const recentLowWindow = numberParam(params, "close_below_recent_low_stop_window");
  const lines = [
    params["entry_on_signal_day"]
      ? "选股日（T 日）按收盘价买入，并计入买入滑点和交易费用。"
      : "选股日为 T 日，T+1 按开盘价买入，并计入买入滑点和交易费用。",
    holdDays === null
      ? "卖出日按当前交易规则执行，并计入卖出滑点和交易费用。"
      : params["entry_on_signal_day"]
        ? `T+${holdDays} 按收盘价卖出（超短线，持股 ${holdDays} 日）。`
        : `默认持仓 ${holdDays} 个交易日，T+${holdDays + 1} 按收盘价卖出，并计入卖出滑点和交易费用。`,
  ];
  if (tradeStrategyName !== "-") {
    lines.unshift(`交易策略为${tradeStrategyName}。`);
  }

  if (params["reject_if_limit_up_on_buy"]) {
    lines.push("买入日若涨停无法成交，则放弃该笔买入。");
  }
  if (params["force_sell_on_two_day_close_below_long_term_bull_bear_line"]) {
    lines.push("持仓期间若连续两日收盘价低于长期多空线，则在第二日按收盘价触发强制卖出。");
  }
  if (recentLowWindow !== null) {
    lines.push(`持仓期间若今日收盘价低于买入后截至昨日最近 ${recentLowWindow} 个交易日最低价，则按今日收盘价触发止损卖出。`);
  }
  if (params["postpone_if_limit_down_on_sell"]) {
    lines.push("如果卖出日跌停无法成交，跌停顺延卖出优先于其他卖出规则。");
  }
  lines.push(describePositionLimits(params, capitalMode));
  lines.push(describeCosts(params));

  if (capitalMode === "unlimited_cash") {
    lines.push(
      `资金模式为${capitalModeLabel(capitalMode)}：每只股票按 ${formatMoney(cashPerTrade)} 元名义金额买入，` +
        `不做现金不足限制；名义金额凑不满最小成交单位时按最小成交单位买入` +
        `（主板 100 股、科创板 200 股），该笔实际投入会超过名义金额。`,
    );
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
    // B1 公式版：直接使用注册表最新描述（旧 params 结构已被公式替换）
    return splitDescription(strategy.description);
  }

  if (strategy.class === "BBIKDJSelector") {
    return splitDescription(strategy.description);
  }

  if (strategy.class === "SuperB1Selector") {
    return splitDescription(strategy.description);
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
  // 旧产物（参数快照机制上线前）没有 effective_config.json，后端只能按【今天】的
  // 默认值重算规则回显。用户显式设过的字段仍准，吃了默认值的字段可能与实跑不符
  // （如印花税默认从 0.01% 改到 0.05%）。这条失真无法恢复，但必须可察觉，不能静默改口。
  const rebuilt = Boolean(params["rebuilt_from_request"]);
  return (
    <Card className="strategy-snapshot-card">
      <Space direction="vertical" size={8}>
        {rebuilt ? (
          <Alert
            type="warning"
            showIcon
            message="此报告早于参数快照机制"
            description="下列交易规则中，你未显式设置的参数按当前默认值回显，可能与该报告实际运行时的取值不符。"
          />
        ) : null}
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
