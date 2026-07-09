from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from backtest.config import RiskConfig


def compute_summary(daily_equity: pd.DataFrame, trades: pd.DataFrame, risk_cfg: RiskConfig) -> Dict[str, float]:
    if daily_equity.empty:
        return {
            "total_return_pct": 0.0,
            "annual_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "trade_count": 0,
            "win_rate_pct": 0.0,
        }

    nav = daily_equity["equity"].astype(float)
    total_return = nav.iloc[-1] / nav.iloc[0] - 1.0 if nav.iloc[0] > 0 else 0.0
    periods = max(1, len(nav) - 1)
    annual_return = (1 + total_return) ** (risk_cfg.trading_days_per_year / periods) - 1.0

    rolling_max = nav.cummax()
    drawdown = nav / rolling_max - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    daily_ret = nav.pct_change().dropna()
    if daily_ret.empty:
        sharpe = 0.0
    elif float(daily_ret.abs().sum()) == 0.0:
        sharpe = 0.0
    else:
        excess_daily = daily_ret - risk_cfg.risk_free_rate / risk_cfg.trading_days_per_year
        vol = excess_daily.std(ddof=1)
        if vol <= 1e-12:
            sharpe = 0.0
        else:
            sharpe = float(np.sqrt(risk_cfg.trading_days_per_year) * excess_daily.mean() / vol)

    win_rate = 0.0
    if not trades.empty:
        win_rate = float((trades["profit"] > 0).mean())

    return {
        "total_return_pct": total_return * 100.0,
        "annual_return_pct": annual_return * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
        "sharpe": sharpe,
        "trade_count": int(len(trades)),
        "win_rate_pct": win_rate * 100.0,
    }
