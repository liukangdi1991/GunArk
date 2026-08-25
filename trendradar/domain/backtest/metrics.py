from __future__ import annotations

from typing import Any, List

import polars as pl

from trendradar.domain.backtest.config import RiskConfig
from trendradar.domain.backtest.models import TradeRecord


def compute_summary(
    equity_curve: list[dict],
    trades: list[TradeRecord],
    risk_cfg: RiskConfig,
) -> dict[str, Any]:
    if not equity_curve:
        return {
            "total_return_pct": 0.0,
            "annual_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "trade_count": 0,
            "win_rate_pct": 0.0,
        }

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

    win_rate = 0.0
    if trades:
        win_count = sum(1 for t in trades if t.profit > 0)
        win_rate = win_count / len(trades)

    return {
        "total_return_pct": total_return * 100.0,
        "annual_return_pct": annual_return * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
        "sharpe": sharpe,
        "trade_count": len(trades),
        "win_rate_pct": win_rate * 100.0,
    }
