from __future__ import annotations

from typing import Any, Iterable, Sequence

import polars as pl

from trendradar.domain.backtest.config import RiskConfig
from trendradar.domain.backtest.models import OpenPositionRecord, TradeRecord

# 组合级净值指标：只有"这个账户现在值多少钱"问得通时才有定义
NAV_METRIC_KEYS = (
    "total_return_pct",
    "annual_return_pct",
    "max_drawdown_pct",
    "sharpe",
    "initial_cash",
    "final_cash",
)


def money_summary(
    trades: Sequence[TradeRecord],
    open_positions: Iterable[OpenPositionRecord] = (),
) -> dict[str, Any]:
    """逐笔金额口径：不依赖权益/现金，unlimited_cash 模式只有这一份账。

    分母用买入面额（buy_price × shares），与报告里按策略的收益率、前端
    「逐笔盈亏」卡片同一口径——三处三个数才是这个函数存在的理由。
    """
    realized = float(sum(t.profit for t in trades))
    notional = float(sum(t.buy_amount for t in trades))
    win_count = sum(1 for t in trades if t.profit > 0)
    return {
        "realized_profit_sum": realized,
        "invested_notional_sum": notional,
        # 一分钱都没投过 ⇒ 收益率没有定义，0.00% 会被读成"不赚不亏"
        "pnl_return_pct": (realized / notional * 100.0) if notional > 0 else None,
        "unrealized_pnl": float(sum(p.unrealized_pnl for p in open_positions)),
        "trade_count": len(trades),
        "win_rate_pct": (win_count / len(trades) * 100.0) if trades else 0.0,
    }


def compute_summary(
    equity_curve: list[dict],
    trades: list[TradeRecord],
    risk_cfg: RiskConfig,
    initial_cash: float | None = None,
    open_positions: Iterable[OpenPositionRecord] = (),
) -> dict[str, Any]:
    summary = money_summary(trades, open_positions)

    if not equity_curve:
        initial = float(initial_cash) if initial_cash is not None else 0.0
        summary.update({
            "total_return_pct": 0.0,
            "annual_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "initial_cash": initial,
            "final_cash": initial,
        })
        return summary

    df = pl.DataFrame(equity_curve)
    nav = df["equity"]

    first_equity = nav[0]
    last_equity = nav[-1]
    total_return = (last_equity / first_equity - 1.0) if first_equity > 0 else 0.0

    periods = max(1, len(nav) - 1)
    # 权益为负时 total_return < -1，(1+total_return) 为负底数分数次幂会产生复数——夹断
    annual_return = (
        -1.0
        if last_equity <= 0
        else (1 + total_return) ** (risk_cfg.trading_days_per_year / periods) - 1.0
    )

    rolling_max = nav.cum_max()
    drawdown = nav / rolling_max - 1.0
    max_drawdown = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    daily_ret = nav / nav.shift(1) - 1.0
    daily_ret = daily_ret.drop_nulls()
    if len(daily_ret) == 0:
        sharpe = 0.0
    elif float(daily_ret.abs().sum()) == 0.0:
        sharpe = 0.0
    else:
        excess_daily = daily_ret - risk_cfg.risk_free_rate / risk_cfg.trading_days_per_year
        vol = excess_daily.std(ddof=1)
        if vol is None or vol <= 1e-12:
            sharpe = 0.0
        else:
            sharpe = float((risk_cfg.trading_days_per_year ** 0.5) * excess_daily.mean() / vol)

    summary.update({
        "total_return_pct": total_return * 100.0,
        "annual_return_pct": annual_return * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
        "sharpe": sharpe,
        "initial_cash": float(initial_cash) if initial_cash is not None else float(first_equity),
        "final_cash": float(last_equity),
    })
    return summary
