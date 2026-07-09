from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest.analytics.metrics import compute_summary
from backtest.config import BacktestConfig
from backtest.data.market_data import MarketDataProvider
from backtest.data.signal_data import list_signal_files, load_signals, signal_file_date
from backtest.execution import FillResult, calc_buy_fill, calc_sell_fill, is_limit_down, is_limit_up
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
    def __init__(self, config: BacktestConfig, signal_files: List[Path] | None = None) -> None:
        self.config = config
        self.signal_dir = Path(config.paths.signal_dir)
        self._explicit_signal_files = [Path(path) for path in signal_files] if signal_files else None
        self.market = MarketDataProvider(
            parquet_dir=Path(config.paths.parquet_dir),
        )
        self._long_term_bull_bear_line_two_day_below_cache: Dict[str, Dict[date, bool]] = {}

    def list_available_strategies(self, start: date, end: date) -> List[str]:
        files = self._list_signal_files(start, end)
        names = set()
        for p in files:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if isinstance(v, dict) and "stocks" in v:
                    names.add(k)
        return sorted(names)

    def run(self, start: date, end: date, strategy_name: str) -> Dict[str, object]:
        signal_files = self._list_signal_files(start, end)
        if not signal_files:
            raise ValueError(f"未找到区间内信号文件: {self._signal_source_label()} [{start} ~ {end}]")

        codes = self._collect_codes(signal_files, strategy_name)
        if not codes:
            return self._build_empty_result(start, end, strategy_name, signal_files)
        self.market.preload(codes)
        full_calendar = self.market.get_calendar()
        self._validate_range_against_holding_rule(
            start=start,
            end=end,
            strategy_name=strategy_name,
            full_calendar=full_calendar,
        )
        calendar = self._build_execution_calendar(start, end, full_calendar)
        if not calendar:
            return self._build_empty_result(start, end, strategy_name, signal_files)

        signals_by_day = load_signals(
            signal_files=signal_files,
            strategy_names=[strategy_name],
            calendar=calendar,
            fixed_hold_n_days=self.config.execution.fixed_hold_n_days,
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
        capital_base = self.config.capital.initial_cash
        if self.config.capital.mode == "unlimited_cash":
            daily_equity, capital_base = self._normalize_unlimited_cash_equity(
                daily_equity=daily_equity,
                trades_df=trades_df,
                state=state,
            )
        summary = compute_summary(daily_equity, trades_df, self.config.risk)
        summary.update(
            {
                "strategy": strategy_name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "simulation_end_date": calendar[-1].isoformat(),
                "capital_mode": self.config.capital.mode,
                "capital_base": float(capital_base),
                "fixed_cash_per_trade": float(self.config.capital.fixed_cash_per_trade),
                "initial_cash": self.config.capital.initial_cash,
                "final_equity": float(daily_equity["equity"].iloc[-1]) if not daily_equity.empty else self.config.capital.initial_cash,
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

    def _list_signal_files(self, start: date, end: date) -> List[Path]:
        if self._explicit_signal_files is None:
            return list_signal_files(self.signal_dir, start, end)
        files: List[Path] = []
        for path in self._explicit_signal_files:
            try:
                signal_date = signal_file_date(path)
            except ValueError:
                continue
            if start <= signal_date <= end:
                files.append(path)
        return sorted(files, key=signal_file_date)

    def _signal_source_label(self) -> str:
        if self._explicit_signal_files is not None:
            return "指定选股结果"
        return str(self.signal_dir)

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
                d = signal_file_date(p)
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
                "simulation_end_date": end.isoformat(),
                "capital_mode": self.config.capital.mode,
                "capital_base": self.config.capital.initial_cash,
                "fixed_cash_per_trade": float(self.config.capital.fixed_cash_per_trade),
                "initial_cash": self.config.capital.initial_cash,
                "final_equity": self.config.capital.initial_cash,
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

    def _normalize_unlimited_cash_equity(
        self,
        *,
        daily_equity: pd.DataFrame,
        trades_df: pd.DataFrame,
        state: PortfolioState,
    ) -> Tuple[pd.DataFrame, float]:
        """Use total deployed capital as the denominator in unlimited-cash mode."""
        if daily_equity.empty:
            return daily_equity, self.config.capital.initial_cash

        closed_cost = 0.0
        if not trades_df.empty and "total_cost" in trades_df.columns:
            closed_cost = float(trades_df["total_cost"].astype(float).sum())
        open_cost = float(sum(pos.entry_cost for pos in state.positions.values()))
        capital_base = closed_cost + open_cost
        if capital_base <= 0:
            capital_base = self.config.capital.initial_cash

        normalized = daily_equity.copy()
        raw_initial_equity = float(normalized["equity"].astype(float).iloc[0])
        normalized["raw_equity"] = normalized["equity"].astype(float)
        normalized["capital_base"] = capital_base
        normalized["equity"] = capital_base + (normalized["raw_equity"] - raw_initial_equity)
        return normalized, capital_base

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
                        buy_date=cur_date,
                        stage="buy",
                        reason="停牌或无行情",
                        date_ref=cur_date,
                    )
                )
                continue

            prev_close = self.market.get_previous_close(code, cur_date)
            if self.config.execution.reject_if_limit_up_on_buy and is_limit_up(row["open"], prev_close, code=code):
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        buy_date=cur_date,
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
            if self.config.capital.mode == "unlimited_cash":
                budget = float(self.config.capital.fixed_cash_per_trade)
            else:
                budget = calc_position_budget(self.config, equity, state.cash)
            open_price = float(row["open"])
            lot_size = self.config.capital.lot_size
            shares = int(budget / open_price / lot_size) * lot_size
            if shares <= 0:
                skips.append(
                    SkipRecord(
                        strategy=sig.strategy,
                        code=code,
                        signal_date=sig.signal_date,
                        buy_date=cur_date,
                        stage="buy",
                        reason="资金预算不足",
                        date_ref=cur_date,
                    )
                )
                continue

            if self.config.capital.mode == "unlimited_cash":
                buy_fill = calc_buy_fill(open_price, shares, self.config.costs)
                cash_needed = buy_fill.amount + buy_fill.fee
            else:
                buy_fill, shares = self._fit_buy_fill_to_cash(open_price, shares, state.cash)
                if buy_fill is None or shares <= 0:
                    skips.append(
                        SkipRecord(
                            strategy=sig.strategy,
                            code=code,
                            signal_date=sig.signal_date,
                            buy_date=cur_date,
                            stage="buy",
                            reason="现金不足",
                            date_ref=cur_date,
                        )
                    )
                    continue
                cash_needed = buy_fill.amount + buy_fill.fee

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

    def _fit_buy_fill_to_cash(
        self,
        open_price: float,
        shares: int,
        cash: float,
    ) -> Tuple[Optional[FillResult], int]:
        lot_size = self.config.capital.lot_size
        test_shares = shares
        while test_shares > 0:
            fill = calc_buy_fill(open_price, test_shares, self.config.costs)
            if fill.amount + fill.fee <= cash:
                return fill, test_shares
            test_shares -= lot_size
        return None, 0

    def _build_execution_calendar(self, start: date, end: date, full_calendar: List[date]) -> List[date]:
        in_window_idx = [i for i, d in enumerate(full_calendar) if start <= d <= end]
        if not in_window_idx:
            return []

        start_idx = in_window_idx[0]
        end_idx = in_window_idx[-1]
        horizon_days = (
            self.config.execution.fixed_hold_n_days
            + 1
            + self.config.execution.max_sell_postpone_days
        )
        exec_end_idx = min(len(full_calendar) - 1, end_idx + max(0, horizon_days))
        return full_calendar[start_idx : exec_end_idx + 1]

    def _validate_range_against_holding_rule(
        self,
        start: date,
        end: date,
        strategy_name: str,
        full_calendar: List[date],
    ) -> None:
        if not full_calendar:
            raise ValueError(f"{strategy_name}: 行情交易日为空，无法回测")

        latest_market_date = full_calendar[-1]
        if end > latest_market_date:
            raise ValueError(
                f"{strategy_name}: 回测结束日 {end} 晚于最新行情日 {latest_market_date}，请调整 --to"
            )

        required_future_days = self.config.execution.fixed_hold_n_days + 1
        if len(full_calendar) <= required_future_days:
            raise ValueError(
                f"{strategy_name}: 行情交易日不足，至少需要 {required_future_days + 1} 个交易日"
            )

        latest_allowed_end = full_calendar[-1 - required_future_days]
        if end > latest_allowed_end:
            sell_offset_days = self.config.execution.fixed_hold_n_days + 1
            raise ValueError(
                f"{strategy_name}: 结束日 {end} 超出可回测上限 {latest_allowed_end}。"
                f"当前规则为 T 日信号、T+1 开盘买入、T+{sell_offset_days} 收盘卖出"
                f"(即持有 N={self.config.execution.fixed_hold_n_days} 天后卖出)。"
                f"请使用 --to <= {latest_allowed_end}"
            )

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
            row = self.market.get_row(code, cur_date)
            if row is None:
                continue
            trigger_by_hold = cur_date >= pos.target_sell_date
            trigger_by_zx = (
                self.config.execution.force_sell_on_two_day_close_below_long_term_bull_bear_line
                and self._is_two_day_close_below_long_term_bull_bear_line(code, cur_date)
            )
            trigger_by_recent_low = self._is_close_below_recent_low_stop(code, cur_date, pos)
            if not (trigger_by_hold or trigger_by_zx or trigger_by_recent_low):
                continue

            can_sell = True
            prev_close = self.market.get_previous_close(code, cur_date)
            if self.config.execution.postpone_if_limit_down_on_sell and is_limit_down(row["close"], prev_close, code=code):
                pos.planned_sell_attempts += 1
                can_sell = False
                if pos.planned_sell_attempts > self.config.execution.max_sell_postpone_days:
                    can_sell = True
                    reason = "跌停顺延超上限，按收盘强制平仓"
                    if trigger_by_zx and not trigger_by_hold:
                        reason = "长期多空线连续两日跌破触发卖出，但跌停顺延超上限，按收盘强制平仓"
                    if trigger_by_recent_low and not trigger_by_hold:
                        reason = "10日低点止损触发卖出，但跌停顺延超上限，按收盘强制平仓"
                    skips.append(
                        SkipRecord(
                            strategy=pos.strategy,
                            code=pos.code,
                            signal_date=pos.signal_date,
                            buy_date=pos.entry_date,
                            stage="sell",
                            reason=reason,
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

    def _is_two_day_close_below_long_term_bull_bear_line(self, code: str, cur_date: date) -> bool:
        cache = self._long_term_bull_bear_line_two_day_below_cache.get(code)
        if cache is None:
            cache = self._build_two_day_long_term_bull_bear_line_below_map(code)
            self._long_term_bull_bear_line_two_day_below_cache[code] = cache
        return bool(cache.get(cur_date, False))

    def _build_two_day_long_term_bull_bear_line_below_map(self, code: str) -> Dict[date, bool]:
        df = self.market.load_code(code)
        if df is None or df.empty:
            return {}

        close = df["close"].astype(float)
        long_term_bull_bear_line = (
            close.rolling(window=14, min_periods=14).mean()
            + close.rolling(window=28, min_periods=28).mean()
            + close.rolling(window=57, min_periods=57).mean()
            + close.rolling(window=114, min_periods=114).mean()
        ) / 4.0
        below = close < long_term_bull_bear_line
        two_day = below & below.shift(1, fill_value=False)

        result: Dict[date, bool] = {}
        for d, v in zip(df["date"], two_day):
            result[d] = bool(v)
        return result

    def _is_close_below_recent_low_stop(self, code: str, cur_date: date, pos: Position) -> bool:
        window = self.config.execution.close_below_recent_low_stop_window
        if window is None or window <= 0:
            return False

        df = self.market.load_code(code)
        if df is None or df.empty:
            return False

        ordered = df.copy()
        ordered["date"] = pd.to_datetime(ordered["date"]).dt.date
        ordered = ordered.sort_values("date").reset_index(drop=True)

        today = ordered[ordered["date"] == cur_date]
        if today.empty:
            return False

        prior_holding = ordered[(ordered["date"] >= pos.entry_date) & (ordered["date"] < cur_date)]
        if prior_holding.empty:
            return False

        recent_low = float(prior_holding.tail(int(window))["low"].astype(float).min())
        today_close = float(today.iloc[-1]["close"])
        return today_close < recent_low
