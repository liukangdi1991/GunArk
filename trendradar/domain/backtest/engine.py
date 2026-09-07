from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, List, Optional

import polars as pl
from trendradar.domain.backtest.config import BacktestConfig
from trendradar.domain.backtest.execution import (
    FillResult,
    calc_buy_fill,
    calc_sell_fill,
    is_limit_down,
    is_limit_up,
    min_trading_shares,
)
from trendradar.domain.backtest.models import (
    OpenPositionRecord,
    Position,
    SkipRecord,
    TradeRecord,
)
from trendradar.domain.backtest.portfolio import (
    PortfolioState,
    PositionKey,
    available_open_slots,
    current_equity,
    filter_reentry_keys,
    is_holding,
)
from trendradar.domain.signal.models import SignalSet
from trendradar.domain.market.data_store import MarketDataStore


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    skips: list[SkipRecord] = field(default_factory=list)
    open_positions: list[OpenPositionRecord] = field(default_factory=list)
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
        calendar = market_store.get_calendar()
        if not calendar:
            return self._no_trade_result()

        signals_by_date = self._group_signals_by_date(signal_set, calendar)
        if not signals_by_date:
            return self._no_trade_result()

        state = PortfolioState(cash=self.config.capital.initial_cash)
        trades: list[TradeRecord] = []
        skips: list[SkipRecord] = []
        equity_curve: list[dict] = []
        # unlimited_cash 不限现金（实测首日 −2.33 亿），"这个账户现在值多少钱"问不通，
        # 于是不产净值曲线、只算逐笔盈亏。见 2026-09-04 那份 unlimited-cash 设计。
        tracks_nav = self.config.capital.mode != "unlimited_cash"
        cal_index = {d: i for i, d in enumerate(calendar)}
        # 从首个买入日开始、到最后一个买入日之后持仓了结为止
        # （信号前后的平线段不进 equity，年化指标不被稀释）
        start_idx = min(cal_index[d] for d in signals_by_date)
        last_buy_idx = max(cal_index[d] for d in signals_by_date)
        total = len(calendar) - start_idx

        for idx, cur_date in enumerate(calendar[start_idx:], start=start_idx):
            if cancel_check and cancel_check():
                break
            if progress:
                progress(idx - start_idx + 1, total)

            self._process_exits(cur_date, state, trades, cal_index, market_store)
            self._process_entries(cur_date, signals_by_date.get(cur_date, []), state, skips, market_store)

            if tracks_nav:
                mark_prices: dict[PositionKey, float] = {}
                for key, pos in state.positions.items():
                    mark_prices[key] = self._mark(market_store, pos, cur_date)
                equity = current_equity(state, mark_prices)
                equity_curve.append(
                    {
                        "date": cur_date,
                        "cash": state.cash,
                        "equity": equity,
                        "position_count": len(state.positions),
                    }
                )
            if idx >= last_buy_idx and not state.positions:
                break  # 无持仓且不再有买入 → 结束（含跌停顺延后的了结）

        if not tracks_nav:
            # 不日标 ≠ 不标：标记价靠 _mark 推进 pos.last_close，期末统一标一次，
            # 「期末未平仓」的标记价/浮动盈亏才不会停在成本上
            for pos in state.positions.values():
                self._mark_final(market_store, pos, cur_date)

        open_positions = [self._open_record(pos, cal_index, cur_date)
                          for pos in state.positions.values()]
        metrics = self._compute_metrics(trades, equity_curve, open_positions)
        return BacktestResult(trades=trades, skips=skips, open_positions=open_positions,
                              equity_curve=equity_curve, metrics=metrics)

    def _no_trade_result(self) -> BacktestResult:
        """没有任何可交易信号时也要给出指标：空的 metrics dict 会让报告把初始资金显示成 0。"""
        return BacktestResult(metrics=self._compute_metrics([], [], []))

    def _group_signals_by_date(
        self, signal_set: SignalSet, calendar: list[date]
    ) -> dict[date, list[dict]]:
        """Map signals to buy dates using trading-day index arithmetic.

        V1 rule: signal at T, buy at T+1 open (or T close when
        entry_on_signal_day), target sell at buy+N close. All indices are
        trading-day indices. Signals whose buy or target date falls beyond the
        calendar are skipped.
        """
        cal_index = {d: i for i, d in enumerate(calendar)}
        hold = self.config.execution.fixed_hold_n_days
        entry_offset = 0 if self.config.execution.entry_on_signal_day else 1
        result: dict[date, list[dict]] = {}
        for sig in signal_set.signals:
            if sig.signal_date is None or not sig.codes:
                continue
            sig_idx = cal_index.get(sig.signal_date)
            if sig_idx is None:
                continue
            buy_idx = sig_idx + entry_offset
            sell_idx = buy_idx + hold
            if buy_idx >= len(calendar) or sell_idx >= len(calendar):
                continue
            buy_date = calendar[buy_idx]
            target_sell_date = calendar[sell_idx]
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

    @staticmethod
    def _to_rows(data):
        """Normalize get_rows return to list[dict]."""
        if isinstance(data, pl.DataFrame):
            return data.to_dicts()
        return list(data) if data else []

    def _mark(self, market_store: MarketDataStore, pos: Position, dt: date) -> float:
        """标记价 = 最后一根真实收盘价，停牌时沿用上一根。

        原来无当日行情就回落到 entry_price：停牌第一天把浮盈凭空抹平（曲线上是一
        条没发生过的回撤），退市股更惨——净值永久冻结在成本，实际接近归零。
        """
        row = market_store.get_row(pos.code, dt)
        if row is not None:
            pos.last_close = float(row.get("close", pos.last_close))
            pos.last_close_date = dt
        return pos.last_close if pos.last_close > 0 else pos.entry_price

    def _mark_final(
        self, market_store: MarketDataStore, pos: Position, until: date
    ) -> None:
        """期末补标：标记价 = `until` 之前**最后一根**真实收盘价。

        不能像 `_mark` 那样只看 `until` 这一天——到期还卖不掉的票，恰恰常是那天
        没有 K 线的停牌股（`_mark` 取不到当日行情就不推进 `last_close`，于是标记价
        永远停在成本，浮动盈亏恒为 0）。
        """
        rows = self._to_rows(market_store.get_rows(pos.code, pos.entry_date, until))
        if not rows:
            return
        last = rows[-1]
        close = float(last.get("close", 0) or 0)
        if close <= 0:
            return
        pos.last_close = close
        pos.last_close_date = last.get("date") or until

    @staticmethod
    def _mark_blocked(pos: Position, dt: date, reason: str) -> None:
        if pos.blocked_since is None:
            pos.blocked_since = dt
        pos.blocked_reason = reason

    def _open_record(self, pos: Position, cal_index: dict[date, int], until: date) -> OpenPositionRecord:
        mark = pos.last_close if pos.last_close > 0 else pos.entry_price
        pnl = mark * pos.shares - pos.entry_cost
        if pos.blocked_since and pos.blocked_since in cal_index and until in cal_index:
            blocked_days = cal_index[until] - cal_index[pos.blocked_since] + 1
        else:
            blocked_days = 0
        return OpenPositionRecord(
            strategy=pos.strategy,
            code=pos.code,
            signal_date=pos.signal_date,
            buy_date=pos.entry_date,
            target_sell_date=pos.target_sell_date,
            shares=pos.shares,
            entry_price=pos.entry_price,
            entry_cost=pos.entry_cost,
            mark_price=mark,
            mark_date=pos.last_close_date,
            blocked_since=pos.blocked_since,
            blocked_reason=pos.blocked_reason or "未成交",
            blocked_trading_days=blocked_days,
            unrealized_pnl=pnl,
            unrealized_return_pct=(pnl / pos.entry_cost * 100.0) if pos.entry_cost > 0 else 0.0,
        )

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

        keys = [(s["code"], s["strategy"]) for s in day_signals]
        filtered_keys = filter_reentry_keys(state, keys, self.config.portfolio.allow_reentry_same_stock)
        allowed = set(filtered_keys)
        signal_by_key = {(s["code"], s["strategy"]): s for s in day_signals if (s["code"], s["strategy"]) in allowed}
        candidates = sorted(signal_by_key.keys())

        max_daily = self.config.portfolio.max_daily_new_positions
        slots = min(
            len(candidates) if max_daily is None else max_daily,
            available_open_slots(state, self.config.portfolio.max_positions),
        )
        if slots <= 0:
            return

        # 当日每份持仓只标一次价：_mark 要整文件读 parquet，放进候选循环里
        # 就成了 候选数 × 持仓数 次读（全市场实测占满 82% 的回测时间）
        marks: dict[PositionKey, float] = {}

        def mark_prices() -> dict[PositionKey, float]:
            for key, p in state.positions.items():
                if key not in marks:
                    marks[key] = self._mark(market_store, p, cur_date)
            return marks

        opened = 0
        for key in candidates:
            if opened >= slots:
                break
            code, strategy = key
            if is_holding(state, code, strategy):
                continue
            row = market_store.get_row(code, cur_date)
            sig = signal_by_key[key]
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
            # entry_on_signal_day 的信号在收盘后产生，必须按收盘价入场（且按收盘价判涨停）
            entry_at_close = (
                self.config.execution.entry_at_close
                or self.config.execution.entry_on_signal_day
            )
            entry_price = row["close"] if entry_at_close else row["open"]
            if self.config.execution.reject_if_limit_up_on_buy and is_limit_up(entry_price, prev_close, code=code):
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

            open_price = float(entry_price)
            if self.config.capital.mode == "unlimited_cash":
                budget = float(self.config.capital.fixed_cash_per_trade)
            else:
                equity = current_equity(state, mark_prices())
                budget = self._calc_position_budget(equity, state.cash)

            lot_size = self.config.capital.lot_size
            required_shares = max(lot_size, min_trading_shares(code=code))
            shares = int(budget / open_price / lot_size) * lot_size
            if shares < required_shares and self.config.capital.mode == "unlimited_cash":
                # 该模式不校验现金，名义额只是"想投多少"：1300 元的茅台 5 万凑不满
                # 一手，300 元的科创板凑得满一手但 100 股低于 200 股最小成交单位。
                # 都按最小成交单位买满，否则整笔信号消失或下出无效单，报告里还挂
                # 一条在无限资金下站不住脚的"资金预算不足"
                shares = required_shares
            if shares < required_shares:
                # realistic：按手截断后不足最小成交单位 ⇒ 补齐到最小单位的金额
                # 必然超出预算，硬抬会破坏预算约束，只能放弃
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
            state.open_position(code, strategy, position, cash_needed)
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
    ) -> tuple[Optional[FillResult], int]:
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
        cal_index: dict[date, int],
        market_store: MarketDataStore,
    ) -> None:
        to_close: list[tuple[PositionKey, Position]] = []
        for key, pos in state.positions.items():
            code = pos.code
            row = market_store.get_row(code, cur_date)
            if row is None:
                # 没有当日行情就无从判断触发条件。只有"本来就该卖了"（已到计划卖出日，
                # 或已在顺延中）才算卖不掉，否则只是还没到该卖的时候。
                if cur_date >= pos.target_sell_date or pos.blocked_since:
                    self._mark_blocked(pos, cur_date, "停牌无行情")
                continue
            trigger_by_hold = cur_date >= pos.target_sell_date
            trigger_by_zx = (
                self.config.execution.force_sell_on_two_day_close_below_long_term_bull_bear_line
                and self._is_two_day_close_below_long_term_bull_bear_line(market_store, code, cur_date)
            )
            trigger_by_recent_low = self._is_close_below_recent_low_stop(market_store, code, cur_date, pos)
            if not (trigger_by_hold or trigger_by_zx or trigger_by_recent_low):
                pos.blocked_since = None      # 卖出意图解除 → 顺延状态归零，下次从头算
                pos.blocked_reason = ""
                continue

            prev_close = market_store.get_previous_close(code, cur_date)
            if (self.config.execution.postpone_if_limit_down_on_sell
                    and is_limit_down(row["close"], prev_close, code=code)):
                # 顺延到底：跌停价卖不掉，那就别假装卖得掉。旧实现数到上限后按当日
                # 收盘强行成交，等于用同一个价格自己否掉自己的前提。
                self._mark_blocked(pos, cur_date, "跌停封死")
                continue

            to_close.append((key, pos))

        for key, pos in to_close:
            code = pos.code
            row = market_store.get_row(code, cur_date)
            if row is None:
                continue
            sell_fill = calc_sell_fill(float(row["close"]), pos.shares, self.config.costs)
            cash_back = sell_fill.amount - sell_fill.fee
            state.close_position(code, pos.strategy, cash_back)

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

    def _is_two_day_close_below_long_term_bull_bear_line(
        self, market_store: MarketDataStore, code: str, cur_date: date
    ) -> bool:
        today_row = market_store.get_row(code, cur_date)
        if today_row is None:
            return False
        today_close = float(today_row.get("close", 0))
        calendar = market_store.get_calendar()
        idx_map = {d: i for i, d in enumerate(calendar)}
        today_idx = idx_map.get(cur_date)
        if today_idx is None or today_idx < 1:
            return False
        yesterday = calendar[today_idx - 1]
        yesterday_row = market_store.get_row(code, yesterday)
        if yesterday_row is None:
            return False
        yesterday_close = float(yesterday_row.get("close", 0))

        long_term_line_today = self._calc_long_term_bull_bear_line(market_store, code, cur_date)
        long_term_line_yesterday = self._calc_long_term_bull_bear_line(market_store, code, yesterday)
        if long_term_line_today is None or long_term_line_yesterday is None:
            return False
        return today_close < long_term_line_today and yesterday_close < long_term_line_yesterday

    def _calc_long_term_bull_bear_line(
        self, market_store: MarketDataStore, code: str, ref_date: date
    ) -> Optional[float]:
        calendar = market_store.get_calendar()
        idx_map = {d: i for i, d in enumerate(calendar)}
        ref_idx = idx_map.get(ref_date)
        if ref_idx is None or ref_idx < 113:
            return None
        start_idx = max(0, ref_idx - 113)
        start_date = calendar[start_idx]
        rows = self._to_rows(market_store.get_rows(code, start_date, ref_date))
        if len(rows) < 114:
            return None
        closes = [float(r["close"]) for r in rows]
        ma14 = sum(closes[-14:]) / 14
        ma28 = sum(closes[-28:]) / 28
        ma57 = sum(closes[-57:]) / 57
        ma114 = sum(closes[-114:]) / 114
        return (ma14 + ma28 + ma57 + ma114) / 4.0

    def _is_close_below_recent_low_stop(
        self, market_store: MarketDataStore, code: str, cur_date: date, pos: Position
    ) -> bool:
        window = self.config.execution.close_below_recent_low_stop_window
        if window is None or window <= 0:
            return False

        today_row = market_store.get_row(code, cur_date)
        if today_row is None:
            return False
        today_close = float(today_row.get("close", 0))

        rows = self._to_rows(market_store.get_rows(code, pos.entry_date, cur_date))
        holding_rows = [r for r in rows if r.get("date") and r["date"] >= pos.entry_date and r["date"] < cur_date]
        if not holding_rows:
            return False

        recent_low = float(min(r["low"] for r in holding_rows[-window:]))
        return today_close < recent_low

    def _compute_metrics(
        self,
        trades: list[TradeRecord],
        equity_curve: list[dict],
        open_positions: list[OpenPositionRecord],
    ) -> dict[str, Any]:
        from trendradar.domain.backtest.metrics import (
            NAV_METRIC_KEYS,
            compute_summary,
            money_summary,
        )

        if self.config.capital.mode == "unlimited_cash":
            summary = money_summary(trades, open_positions)
            summary.update({key: None for key in NAV_METRIC_KEYS})
            return summary

        if not equity_curve:
            # 曲线为空不等于钱亏了：没有成交时初始/期末现金都应是本金，不能让报告显示 0
            summary = money_summary(trades, open_positions)
            initial = float(self.config.capital.initial_cash)
            summary.update({key: 0.0 for key in NAV_METRIC_KEYS})
            summary["initial_cash"] = initial
            summary["final_cash"] = initial
            return summary

        return compute_summary(
            equity_curve, trades, self.config.risk,
            initial_cash=self.config.capital.initial_cash,
            open_positions=open_positions,
        )
