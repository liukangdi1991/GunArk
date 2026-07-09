from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Protocol

from trendradar.domain.backtest.config import BacktestConfig
from trendradar.domain.backtest.execution import calc_buy_fill, calc_sell_fill, is_limit_down, is_limit_up
from trendradar.domain.backtest.models import Position, SkipRecord, TradeRecord
from trendradar.domain.backtest.portfolio import (
    PortfolioState,
    available_open_slots,
    current_equity,
    filter_reentry_codes,
    is_holding,
)
from trendradar.domain.signal.models import SignalSet


class MarketDataStore(Protocol):
    def get_row(self, code: str, dt: date) -> Optional[dict]: ...
    def get_previous_close(self, code: str, dt: date) -> Optional[float]: ...
    def get_calendar(self) -> List[date]: ...


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    skips: list[SkipRecord] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config

    def run(
        self,
        signal_set: SignalSet,
        market_store: MarketDataStore,
        progress: Callable | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        signals_by_date = self._group_signals_by_date(signal_set)
        if not signals_by_date:
            return BacktestResult()

        calendar = market_store.get_calendar()
        if not calendar:
            return BacktestResult()

        state = PortfolioState(cash=self.config.capital.initial_cash)
        trades: list[TradeRecord] = []
        skips: list[SkipRecord] = []
        equity_curve: list[dict] = []
        cal_index = {d: i for i, d in enumerate(calendar)}

        for cur_date in calendar:
            self._process_exits(cur_date, state, trades, skips, cal_index, market_store)
            self._process_entries(cur_date, signals_by_date.get(cur_date, []), state, skips, market_store)

            mark_prices: dict[str, float] = {}
            for code, pos in state.positions.items():
                mark_prices[code] = self._get_latest_close(market_store, code, cur_date, pos.entry_price)
            equity = current_equity(state, mark_prices)
            equity_curve.append(
                {
                    "date": cur_date,
                    "cash": state.cash,
                    "equity": equity,
                    "position_count": len(state.positions),
                }
            )

        metrics = self._compute_metrics(equity_curve, trades)
        return BacktestResult(trades=trades, skips=skips, equity_curve=equity_curve, metrics=metrics)

    def _group_signals_by_date(self, signal_set: SignalSet) -> dict[date, list[dict]]:
        result: dict[date, list[dict]] = {}
        for sig in signal_set.signals:
            if sig.signal_date is None or not sig.codes:
                continue
            buy_date = sig.signal_date + self._delta_one()
            target_sell_date = self._calc_target_sell_date(sig.signal_date)
            for code in sig.codes:
                result.setdefault(buy_date, []).append(
                    {
                        "strategy": sig.strategy_id,
                        "code": code,
                        "signal_date": sig.signal_date,
                        "target_sell_date": target_sell_date,
                    }
                )
        return result

    def _calc_target_sell_date(self, signal_date: date) -> date:
        from datetime import timedelta
        return signal_date + timedelta(days=self.config.execution.fixed_hold_n_days + 1)

    @staticmethod
    def _delta_one() -> Any:
        from datetime import timedelta
        return timedelta(days=1)

    def _get_latest_close(self, market_store: MarketDataStore, code: str, dt: date, fallback: float) -> float:
        row = market_store.get_row(code, dt)
        if row is not None:
            return float(row.get("close", fallback))
        return fallback

    def _process_entries(
        self,
        cur_date: date,
        day_signals: list[dict],
        state: PortfolioState,
        skips: list[SkipRecord],
        market_store: MarketDataStore,
    ) -> None:
        if not day_signals:
            return

        codes = [s["code"] for s in day_signals]
        filtered_codes = filter_reentry_codes(state, codes, self.config.portfolio.allow_reentry_same_stock)
        signal_by_code = {s["code"]: s for s in day_signals if s["code"] in filtered_codes}
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
            row = market_store.get_row(code, cur_date)
            sig = signal_by_code[code]
            if row is None:
                skips.append(
                    SkipRecord(
                        strategy=sig["strategy"],
                        code=code,
                        signal_date=sig["signal_date"],
                        buy_date=cur_date,
                        stage="buy",
                        reason="停牌或无行情",
                        date_ref=cur_date,
                    )
                )
                continue

            prev_close = market_store.get_previous_close(code, cur_date)
            if self.config.execution.reject_if_limit_up_on_buy and is_limit_up(row["open"], prev_close):
                skips.append(
                    SkipRecord(
                        strategy=sig["strategy"],
                        code=code,
                        signal_date=sig["signal_date"],
                        buy_date=cur_date,
                        stage="buy",
                        reason="涨停无法买入",
                        date_ref=cur_date,
                    )
                )
                continue

            open_price = float(row["open"])
            if self.config.capital.mode == "unlimited_cash":
                budget = float(self.config.capital.fixed_cash_per_trade)
            else:
                mark_prices = {
                    c: self._get_latest_close(market_store, c, cur_date, p.entry_price)
                    for c, p in state.positions.items()
                }
                equity = current_equity(state, mark_prices)
                budget = self._calc_position_budget(equity, state.cash)

            lot_size = self.config.capital.lot_size
            shares = int(budget / open_price / lot_size) * lot_size
            if shares <= 0:
                skips.append(
                    SkipRecord(
                        strategy=sig["strategy"],
                        code=code,
                        signal_date=sig["signal_date"],
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
                            strategy=sig["strategy"],
                            code=code,
                            signal_date=sig["signal_date"],
                            buy_date=cur_date,
                            stage="buy",
                            reason="现金不足",
                            date_ref=cur_date,
                        )
                    )
                    continue
                cash_needed = buy_fill.amount + buy_fill.fee

            position = Position(
                strategy=sig["strategy"],
                code=code,
                signal_date=sig["signal_date"],
                entry_date=cur_date,
                target_sell_date=sig["target_sell_date"],
                entry_price=buy_fill.price,
                shares=shares,
                entry_cost=cash_needed,
            )
            state.open_position(code, position, cash_needed)
            opened += 1

    def _calc_position_budget(self, equity: float, cash: float) -> float:
        caps: list[float] = []
        if self.config.portfolio.target_positions:
            caps.append(equity / self.config.portfolio.target_positions)
        if self.config.portfolio.max_single_position_pct is not None:
            caps.append(equity * self.config.portfolio.max_single_position_pct)
        if self.config.capital.max_cash_usage_pct is not None:
            caps.append(cash * self.config.capital.max_cash_usage_pct)
        if caps:
            return max(0.0, min(caps))
        return max(float(self.config.capital.lot_size), float(self.config.capital.position_budget_cash))

    def _fit_buy_fill_to_cash(
        self,
        open_price: float,
        shares: int,
        cash: float,
    ) -> tuple[object, int]:
        lot_size = self.config.capital.lot_size
        test_shares = shares
        while test_shares > 0:
            fill = calc_buy_fill(open_price, test_shares, self.config.costs)
            if fill.amount + fill.fee <= cash:
                return fill, test_shares
            test_shares -= lot_size
        return None, 0

    def _process_exits(
        self,
        cur_date: date,
        state: PortfolioState,
        trades: list[TradeRecord],
        skips: list[SkipRecord],
        cal_index: dict[date, int],
        market_store: MarketDataStore,
    ) -> None:
        to_close: list[tuple[str, Position]] = []
        for code, pos in state.positions.items():
            row = market_store.get_row(code, cur_date)
            if row is None:
                continue
            trigger_by_hold = cur_date >= pos.target_sell_date
            if not trigger_by_hold:
                continue

            can_sell = True
            prev_close = market_store.get_previous_close(code, cur_date)
            if self.config.execution.postpone_if_limit_down_on_sell and is_limit_down(row["close"], prev_close):
                pos.planned_sell_attempts += 1
                can_sell = False
                if pos.planned_sell_attempts > self.config.execution.max_sell_postpone_days:
                    can_sell = True
                    skips.append(
                        SkipRecord(
                            strategy=pos.strategy,
                            code=pos.code,
                            signal_date=pos.signal_date,
                            buy_date=pos.entry_date,
                            stage="sell",
                            reason="跌停顺延超上限，按收盘强制平仓",
                            date_ref=cur_date,
                        )
                    )

            if not can_sell:
                continue
            to_close.append((code, pos))

        for code, pos in to_close:
            row = market_store.get_row(code, cur_date)
            if row is None:
                continue
            sell_fill = calc_sell_fill(float(row["close"]), pos.shares, self.config.costs)
            cash_back = sell_fill.amount - sell_fill.fee
            state.close_position(code, cash_back)

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

    def _compute_metrics(self, equity_curve: list[dict], trades: list[TradeRecord]) -> dict[str, Any]:
        if not equity_curve:
            return {
                "total_return_pct": 0.0,
                "annual_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe": 0.0,
                "trade_count": 0,
                "win_rate_pct": 0.0,
            }
        from trendradar.domain.backtest.metrics import compute_summary
        return compute_summary(equity_curve, trades, self.config.risk)
