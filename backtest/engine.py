from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from backtest.analytics.metrics import compute_summary
from backtest.config import BacktestConfig
from backtest.data.market_data import MarketDataProvider
from backtest.data.signal_data import list_signal_files, load_signals
from backtest.execution import calc_buy_fill, calc_sell_fill, is_limit_down, is_limit_up
from backtest.models import Position, SkipRecord, TradeRecord
from backtest.portfolio import (
    PortfolioState,
    available_open_slots,
    calc_position_budget,
    close_position,
    current_equity,
    filter_reentry_codes,
    is_holding,
    open_position,
)


class BacktestEngine:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.signal_dir = Path(config.paths.signal_dir)
        self.market = MarketDataProvider(
            parquet_dir=Path(config.paths.parquet_dir),
            csv_dir=Path(config.paths.csv_dir),
        )

    def list_available_strategies(self, start: date, end: date) -> List[str]:
        files = list_signal_files(self.signal_dir, start, end)
        names = set()
        for p in files:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if isinstance(v, dict) and "stocks" in v:
                    names.add(k)
        return sorted(names)

    def run(self, start: date, end: date, strategy_name: str) -> Dict[str, object]:
        signal_files = list_signal_files(self.signal_dir, start, end)
        if not signal_files:
            raise ValueError(f"未找到区间内信号文件: {self.signal_dir} [{start} ~ {end}]")

        codes = self._collect_codes(signal_files, strategy_name)
        if not codes:
            return self._build_empty_result(start, end, strategy_name, signal_files)
        self.market.preload(codes)
        calendar = self.market.get_calendar()
        if not calendar:
            return self._build_empty_result(start, end, strategy_name, signal_files)

        signals_by_day = load_signals(
            signal_files=signal_files,
            strategy_names=[strategy_name],
            calendar=calendar,
            buy_delay_days=self.config.execution.buy_delay_days,
            sell_delay_days=self.config.execution.sell_delay_days,
        )

        state = PortfolioState(cash=self.config.capital.initial_cash)
        trades: List[TradeRecord] = []
        skips: List[SkipRecord] = []
        daily_rows = []
        cal_index = {d: i for i, d in enumerate(calendar)}

        for cur_date in calendar:
            self._process_exits(cur_date, state, trades, skips, cal_index)
            self._process_entries(cur_date, signals_by_day.get(cur_date, []), state, skips)

            mark_prices = {
                code: self.market.get_latest_close(code, cur_date, pos.entry_price)
                for code, pos in state.positions.items()
            }
            equity = current_equity(state, mark_prices)
            daily_rows.append(
                {
                    "date": cur_date,
                    "cash": state.cash,
                    "equity": equity,
                    "position_count": len(state.positions),
                }
            )

        daily_equity = pd.DataFrame(daily_rows)
        trades_df = pd.DataFrame([asdict(t) for t in trades])
        skips_df = pd.DataFrame([asdict(s) for s in skips])
        summary = compute_summary(daily_equity, trades_df, self.config.risk)
        summary.update(
            {
                "strategy": strategy_name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "initial_cash": self.config.capital.initial_cash,
                "final_cash": float(state.cash),
                "open_positions": len(state.positions),
            }
        )
        return {
            "daily_equity": daily_equity,
            "trades": trades_df,
            "skips": skips_df,
            "summary": summary,
        }

    def _build_empty_result(
        self,
        start: date,
        end: date,
        strategy_name: str,
        signal_files: List[Path],
    ) -> Dict[str, object]:
        daily_rows = []
        for p in signal_files:
            try:
                d = datetime.strptime(p.stem, "%Y%m%d").date()
            except ValueError:
                continue
            daily_rows.append(
                {
                    "date": d,
                    "cash": self.config.capital.initial_cash,
                    "equity": self.config.capital.initial_cash,
                    "position_count": 0,
                }
            )
        daily_equity = pd.DataFrame(daily_rows)
        trades_df = pd.DataFrame()
        skips_df = pd.DataFrame()
        summary = compute_summary(daily_equity, trades_df, self.config.risk)
        summary.update(
            {
                "strategy": strategy_name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "initial_cash": self.config.capital.initial_cash,
                "final_cash": self.config.capital.initial_cash,
                "open_positions": 0,
            }
        )
        return {
            "daily_equity": daily_equity,
            "trades": trades_df,
            "skips": skips_df,
            "summary": summary,
        }

    def _collect_codes(self, signal_files: List[Path], strategy_name: str) -> List[str]:
        codes = set()
        for p in signal_files:
            with p.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            s_data = payload.get(strategy_name, {})
            if not isinstance(s_data, dict):
                continue
            for code in s_data.get("stocks", []):
                codes.add(str(code).zfill(6))
        return sorted(codes)

    def _process_entries(
        self,
        cur_date: date,
        day_signals,
        state: PortfolioState,
        skips: List[SkipRecord],
    ) -> None:
        if not day_signals:
            return

        codes = [sig.code for sig in day_signals]
        filtered_codes = filter_reentry_codes(state, codes, self.config.portfolio.allow_reentry_same_stock)
        signal_by_code = {sig.code: sig for sig in day_signals if sig.code in filtered_codes}
        candidates = sorted(signal_by_code.keys())

        slots = min(
            self.config.portfolio.max_daily_new_positions or len(candidates),
            available_open_slots(state, self.config.portfolio.max_positions),
        )
        if slots <= 0:
            return

        opened = 0
        for code in candidates:
            if opened >= slots:
                break
            if is_holding(state, code):
                continue
            row = self.market.get_row(code, cur_date)
            sig = signal_by_code[code]
            if row is None:
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        stage="buy",
                        reason="停牌或无行情",
                        date_ref=cur_date,
                    )
                )
                continue

            if self.config.execution.reject_if_limit_up_on_buy and is_limit_up(row["close"], row["high"]):
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        stage="buy",
                        reason="涨停无法买入",
                        date_ref=cur_date,
                    )
                )
                continue

            mark_prices = {
                c: self.market.get_latest_close(c, cur_date, p.entry_price)
                for c, p in state.positions.items()
            }
            equity = current_equity(state, mark_prices)
            budget = calc_position_budget(self.config, equity, state.cash)
            shares = int(budget / float(row["open"]) / self.config.capital.lot_size) * self.config.capital.lot_size
            if shares <= 0:
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        stage="buy",
                        reason="资金不足",
                        date_ref=cur_date,
                    )
                )
                continue

            buy_fill = calc_buy_fill(float(row["open"]), shares, self.config.costs)
            cash_needed = buy_fill.amount + buy_fill.fee
            if self.config.capital.max_cash_usage_pct is not None and cash_needed > state.cash:
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        stage="buy",
                        reason="现金不足",
                        date_ref=cur_date,
                    )
                )
                continue

            position = Position(
                strategy=sig.strategy,
                code=code,
                signal_date=sig.signal_date,
                entry_date=cur_date,
                target_sell_date=sig.target_sell_date,
                entry_price=buy_fill.price,
                shares=shares,
                entry_cost=cash_needed,
            )
            open_position(state, position, cash_needed)
            opened += 1

    def _process_exits(
        self,
        cur_date: date,
        state: PortfolioState,
        trades: List[TradeRecord],
        skips: List[SkipRecord],
        cal_index: Dict[date, int],
    ) -> None:
        to_close: List[Tuple[str, Position]] = []
        for code, pos in state.positions.items():
            if cur_date < pos.target_sell_date:
                continue

            row = self.market.get_row(code, cur_date)
            if row is None:
                continue

            can_sell = True
            if self.config.execution.postpone_if_limit_down_on_sell and is_limit_down(row["close"], row["low"]):
                pos.planned_sell_attempts += 1
                can_sell = False
                if pos.planned_sell_attempts > self.config.execution.max_sell_postpone_days:
                    can_sell = True
                    skips.append(
                        SkipRecord(
                            strategy=pos.strategy,
                            code=pos.code,
                            signal_date=pos.signal_date,
                            stage="sell",
                            reason="跌停顺延超上限，按收盘强制平仓",
                            date_ref=cur_date,
                        )
                    )

            if not can_sell:
                continue
            to_close.append((code, pos))

        for code, pos in to_close:
            row = self.market.get_row(code, cur_date)
            if row is None:
                continue
            sell_fill = calc_sell_fill(float(row["close"]), pos.shares, self.config.costs)
            cash_back = sell_fill.amount - sell_fill.fee
            close_position(state, code, cash_back, self.config.portfolio.allow_reentry_same_stock)

            total_fee = (pos.entry_cost - pos.entry_price * pos.shares) + sell_fill.fee
            profit = cash_back - pos.entry_cost
            return_pct = (profit / pos.entry_cost * 100.0) if pos.entry_cost > 0 else 0.0
            postpone_days = max(0, cal_index[cur_date] - cal_index[pos.target_sell_date])
            trades.append(
                TradeRecord(
                    strategy=pos.strategy,
                    code=pos.code,
                    signal_date=pos.signal_date,
                    buy_date=pos.entry_date,
                    sell_date=cur_date,
                    buy_price=pos.entry_price,
                    sell_price=sell_fill.price,
                    shares=pos.shares,
                    buy_amount=pos.entry_price * pos.shares,
                    sell_amount=sell_fill.amount,
                    total_cost=pos.entry_cost,
                    total_fee=total_fee,
                    profit=profit,
                    return_pct=return_pct,
                    sell_postpone_days=postpone_days,
                )
            )
