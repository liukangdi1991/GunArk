"""Backtest report payload completeness.

The frontend types (BacktestTrade/BacktestEquity/BacktestSkip/
BacktestOpenPosition) require execution_key (+ strategy on equity), and the
report UI renders sell_postpone_days as the "延期" column. The worker must
serialize all of them. Which *metrics* it may serialize depends on the capital
mode: realistic carries an equity curve and portfolio NAV metrics
(initial_cash/final_cash included, because the presenter reads them straight off
disk), while unlimited_cash is a per-signal sampler and only carries per-trade
money (see 2026-09-04-unlimited-cash-money-only-metrics-design.md).
"""

import contextlib
import json
from datetime import date, timedelta

import pytest

from trendradar.domain.backtest.config import (
    BacktestConfig,
    CapitalConfig,
    ExecutionConfig,
    PortfolioConfig,
)
from trendradar.domain.signal.models import SignalSet, StrategySignal
from trendradar.app.services.backtest_service import _run_backtest_worker

BLOCKED_KEY = "bt-blocked-test"


class _FakeCtx:
    def __init__(self, job_id):
        self.job_id = job_id
        self.messages = []
        self.result = None

    def log(self, message, level="INFO"):
        self.messages.append(message)

    def update_progress(self, current, total, message=""):
        pass

    def check_cancelled(self):
        return False

    def fail(self, error):
        raise AssertionError(error)

    def succeed(self, result):
        self.result = result


def _make_market(days):
    bars = {}
    for d in days:
        bars[d] = {
            "code": "000001", "date": d,
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.4,
            "volume": 1_000_000.0,
        }

    class FakeMarket:
        def get_calendar(self):
            return list(days)

        def get_row(self, code, dt):
            return bars.get(dt)

        def get_rows(self, code, start_dt, end_dt):
            return [bars[d] for d in days if start_dt <= d <= end_dt]

        def get_previous_close(self, code, dt):
            idx = days.index(dt)
            if idx == 0:
                return None
            return bars[days[idx - 1]]["close"]

    return FakeMarket()


def _make_crashing_market(days):
    """day0/1 收 10.0，之后连续跌停（10% 档）且到末尾不打开。"""
    bars, prev = {}, 10.0
    for i, d in enumerate(days):
        close = 10.0 if i < 2 else round(prev * 0.9, 2)
        bars[d] = {"code": "000001", "date": d, "open": close, "high": close * 1.01,
                   "low": close, "close": close, "volume": 1_000_000.0}
        prev = close

    class FakeMarket:
        def get_calendar(self):
            return list(days)

        def get_row(self, code, dt):
            return bars.get(dt)

        def get_rows(self, code, start_dt, end_dt):
            return [bars[d] for d in days if start_dt <= d <= end_dt]

        def get_previous_close(self, code, dt):
            idx = days.index(dt)
            return None if idx == 0 else bars[days[idx - 1]]["close"]

    return FakeMarket()


def _run_backtest_to_disk(tmp_path, monkeypatch, key, market, config):
    """把一次回测跑到底并落盘，返回 (产物目录, 报告 result)。"""
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    ctx = _FakeCtx(key)
    signal_set = SignalSet(
        # 组合管线里选股与回测共用 job_id，此时跳过血缘链接
        execution_key=key,
        signals=[StrategySignal(
            strategy_id="s1", strategy_name="S1", signal_date=days[0], codes=["000001"],
        )],
    )
    result_path = (
        tmp_path / "storage" / "objects" / "executions" / key
        / "backtest" / "result.json"
    )

    _run_backtest_worker(ctx, signal_set, market, config, str(result_path))
    return result_path.parent, ctx.result


def _run_blocked_backtest(tmp_path, monkeypatch):
    """「到期卖不掉」的回测：跌停一路封到行情末尾。"""
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    return _run_backtest_to_disk(
        tmp_path, monkeypatch, BLOCKED_KEY, _make_crashing_market(days),
        BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
    )


def test_worker_report_serializes_contract_fields():
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    ctx = _FakeCtx("bt-report-test")
    signal_set = SignalSet(
        execution_key="sel-1",
        signals=[StrategySignal(
            strategy_id="s1", strategy_name="S1", signal_date=days[0], codes=["000001"],
        )],
    )
    config = BacktestConfig(
        capital=CapitalConfig(mode="realistic"),
        execution=ExecutionConfig(fixed_hold_n_days=1),
    )

    _run_backtest_worker(ctx, signal_set, _make_market(days), config)

    out = ctx.result
    assert out is not None
    assert len(out["trades"]) == 1
    trade = out["trades"][0]
    assert trade["execution_key"] == "bt-report-test"
    assert trade["sell_postpone_days"] == 0
    assert out["equity_curve"], "equity curve must be non-empty"
    eq = out["equity_curve"][0]
    assert eq["execution_key"] == "bt-report-test"
    assert "strategy" in eq
    assert out["metrics"]["initial_cash"] == config.capital.initial_cash
    assert out["metrics"]["final_cash"] > 0
    assert out["open_positions"] == []


def test_worker_serializes_positions_still_blocked_at_end():
    """卖不掉的持仓必须单独出现在报告里：它占着钱、算在净值里，但没有成交可对账。"""
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    ctx = _FakeCtx("bt-open-test")
    signal_set = SignalSet(
        execution_key="sel-1",
        signals=[StrategySignal(
            strategy_id="s1", strategy_name="S1", signal_date=days[0], codes=["000001"],
        )],
    )
    config = BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1))

    _run_backtest_worker(ctx, signal_set, _make_crashing_market(days), config)

    out = ctx.result
    assert out["trades"] == []                       # 一次都没成交
    assert out["skip_count"] == 0                    # 也不再报"强制平仓"
    assert out["metrics"]["open_position_count"] == 1
    op = out["open_positions"][0]
    assert op["execution_key"] == "bt-open-test"
    assert set(op) == {
        "execution_key", "strategy", "code", "signal_date", "buy_date",
        "target_sell_date", "shares", "entry_price", "entry_cost", "mark_price",
        "mark_date", "blocked_since", "blocked_reason", "blocked_trading_days",
        "unrealized_pnl", "unrealized_return_pct",
    }
    assert op["blocked_reason"] == "跌停封死"
    assert op["blocked_trading_days"] > 0
    assert op["unrealized_return_pct"] < 0
    assert op["mark_price"] < op["entry_price"]
    # 这只卖不掉的票必须进金额口径的账（unlimited 模式没有"组合终值"可言）
    assert out["metrics"]["unrealized_pnl"] == op["unrealized_pnl"]
    assert out["metrics"]["final_cash"] is None
    assert out["equity_curve"] == []
    complete = [m for m in ctx.messages if m.startswith("Backtest complete")]
    assert complete and "total_return=" not in complete[0]   # 不拿 N-A 凑一个百分比
    assert any("000001" in m and "跌停封死" in m for m in ctx.messages)


def test_unlimited_worker_serializes_money_not_nav(tmp_path, monkeypatch):
    """unlimited 模式没有曲线，也就没有 equity.parquet；能落盘的只有逐笔金额。"""
    import polars as pl

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    bt_dir, out = _run_backtest_to_disk(
        tmp_path, monkeypatch, "bt-money-test", _make_market(days),
        BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
    )

    assert out["equity_curve"] == []
    assert not (bt_dir / "equity.parquet").exists()
    assert pl.read_parquet(bt_dir / "trades.parquet").height == 1

    m = out["metrics"]
    trade = out["trades"][0]
    notional = trade["buy_price"] * trade["shares"]
    assert m["total_return_pct"] is None and m["sharpe"] is None
    assert m["final_cash"] is None and m["initial_cash"] is None
    assert m["invested_notional_sum"] == notional
    assert m["realized_profit_sum"] == trade["profit"]
    assert m["pnl_return_pct"] == pytest.approx(trade["profit"] / notional * 100.0)
    assert json.loads((bt_dir / "metrics.json").read_text(encoding="utf-8")) == m


def test_realistic_worker_still_serializes_curve_and_nav(tmp_path, monkeypatch):
    """realistic 模式一切照旧——曲线是它的主产物，金额口径顺带也给。"""
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    bt_dir, out = _run_backtest_to_disk(
        tmp_path, monkeypatch, "bt-nav-test", _make_market(days),
        BacktestConfig(
            capital=CapitalConfig(mode="realistic"),
            execution=ExecutionConfig(fixed_hold_n_days=1),
        ),
    )

    assert (bt_dir / "equity.parquet").exists()
    assert out["equity_curve"]
    m = out["metrics"]
    assert isinstance(m["total_return_pct"], float) and isinstance(m["sharpe"], float)
    assert m["final_cash"] == out["equity_curve"][-1]["equity"]
    assert m["invested_notional_sum"] > 0


def test_written_artifacts_agree_with_report_payload(tmp_path, monkeypatch):
    """落盘的 metrics.json / parquet 是给审计用的，必须和报告同一口径。

    metrics.json 曾写引擎原始 dict，与报告里的 metrics 是两个来源：
    final_cash 差一整个未平仓仓位，新增的 open_position_count 也到不了磁盘。
    """
    import polars as pl

    bt_dir, out = _run_blocked_backtest(tmp_path, monkeypatch)

    disk_metrics = json.loads((bt_dir / "metrics.json").read_text(encoding="utf-8"))
    assert disk_metrics == out["metrics"]
    assert disk_metrics["open_position_count"] == 1

    rows = pl.read_parquet(bt_dir / "open_positions.parquet").to_dicts()
    assert [r["blocked_reason"] for r in rows] == ["跌停封死"]


def test_report_payload_reads_blocked_positions_from_disk(tmp_path, monkeypatch):
    """写盘与读盘必须同一口径；历史 result.json 没这个字段时也不能炸。"""
    bt_dir, out = _run_blocked_backtest(tmp_path, monkeypatch)

    from trendradar.interfaces.api.presenters import backtest_result_payload

    payload = backtest_result_payload(BLOCKED_KEY, include_report=True)
    assert set(payload) == {
        "result", "artifacts", "trades", "equity", "skips", "open_positions",
    }
    rows = payload["open_positions"]
    assert [r["blocked_reason"] for r in rows] == ["跌停封死"]
    assert rows[0]["mark_date"] == out["open_positions"][0]["mark_date"]
    assert payload["result"]["summary"][0]["open_positions"] == 1

    legacy = json.loads((bt_dir / "result.json").read_text(encoding="utf-8"))
    legacy.pop("open_positions")
    (bt_dir / "result.json").write_text(json.dumps(legacy), encoding="utf-8")

    legacy_payload = backtest_result_payload(BLOCKED_KEY, include_report=True)
    assert legacy_payload["open_positions"] == []
    assert legacy_payload["result"]["summary"] == []


def test_report_window_survives_missing_equity_curve(tmp_path, monkeypatch):
    """回测区间原来取曲线首末点——unlimited 模式没曲线，区间不能因此空白。"""
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    _run_blocked_backtest(tmp_path, monkeypatch)

    from trendradar.interfaces.api.presenters import backtest_result_payload

    run = backtest_result_payload(BLOCKED_KEY)
    assert run["summary"][0] or run["strategies"]
    assert run["start_date"] == str(days[1])     # 首个买入日
    assert run["end_date"] == str(days[7])       # 最后一个有行情的日子


def test_unlimited_summary_rows_carry_money_not_copied_nav(tmp_path, monkeypatch):
    """组合级净值逐行复制＝把同一个假数贴 N 遍；unlimited 模式下它是 N-A。"""
    _run_blocked_backtest(tmp_path, monkeypatch)

    from trendradar.interfaces.api.presenters import backtest_result_payload

    payload = backtest_result_payload(BLOCKED_KEY, include_report=True)
    row = payload["result"]["summary"][0]

    assert row["capital_mode"] == "unlimited_cash"
    for key in ("annual_return_pct", "max_drawdown_pct", "sharpe",
                "final_cash", "initial_cash"):
        assert row[key] is None, key
    assert row["realized_profit_sum"] == 0.0            # 一笔都没成交
    assert row["invested_notional_sum"] == 0.0
    assert row["unrealized_pnl"] == pytest.approx(
        payload["open_positions"][0]["unrealized_pnl"])
    assert row["unrealized_pnl"] < 0


def test_backtest_config_reads_nested_capital(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    conn = sc.connect()
    conn.execute(
        "INSERT INTO jobs (job_id, job_type, status, request_json) VALUES (?, ?, ?, ?)",
        (
            "job_selbt", "selection_backtest", "success",
            json.dumps({
                "start_date": "2026-08-01", "end_date": "2026-08-30",
                "backtest": {
                    "capital": {"mode": "realistic", "fixed_cash_per_trade": 80000},
                    "execution": {},
                },
                "trade_strategy": None,
            }),
        ),
    )
    conn.execute(
        "INSERT INTO jobs (job_id, job_type, status, request_json) VALUES (?, ?, ?, ?)",
        (
            "job_btfs", "backtest", "success",
            json.dumps({
                "execution_key": "sel-1",
                "capital": {"mode": "realistic", "fixed_cash_per_trade": 60000},
                "execution": {},
                "trade_strategy": None,
            }),
        ),
    )
    conn.commit()
    conn.close()

    from trendradar.interfaces.api.presenters import _backtest_config

    nested = _backtest_config("job_selbt")
    assert nested["capital_mode"] == "realistic"
    assert nested["cash_per_trade"] == 80000

    top = _backtest_config("job_btfs")
    assert top["capital_mode"] == "realistic"
    assert top["cash_per_trade"] == 60000


# ---------------------------------------------------------------------------
# 决策②：报告行以 id 为机器键，展示名由 API 层补 strategy_name
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _strategy_s1_registered(name: str = "测试策略"):
    import trendradar.domain.strategy.registry as registry
    from trendradar.domain.strategy.models import StrategyDefinition

    before = dict(registry._registry)
    registry.register(StrategyDefinition(
        strategy_id="s1", name=name, description="",
        selector_class=type("Fake", (), {}),
    ))
    try:
        yield
    finally:
        registry._registry.clear()
        registry._registry.update(before)


def test_report_summary_and_trades_carry_strategy_name(tmp_path, monkeypatch):
    """选股页显示中文名、回测页显示英文 id 是同一策略两套叫法；行里补 strategy_name，
    但 strategy 仍是 id——前端拿它当 rowKey/筛选键，不能换成会重名的中文。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    with _strategy_s1_registered():
        _run_backtest_to_disk(
            tmp_path, monkeypatch, BLOCKED_KEY, _make_market(days),
            BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
        )
        payload = backtest_result_payload(BLOCKED_KEY, include_report=True)

    row = payload["result"]["summary"][0]
    assert row["strategy"] == "s1"
    assert row["strategy_name"] == "测试策略"
    assert payload["result"]["strategies"] == ["测试策略"]
    assert payload["trades"][0]["strategy"] == "s1"
    assert payload["trades"][0]["strategy_name"] == "测试策略"


def test_report_open_positions_carry_strategy_name(tmp_path, monkeypatch):
    """「期末未平仓」表走同一条补名路径。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    with _strategy_s1_registered():
        _run_blocked_backtest(tmp_path, monkeypatch)
        payload = backtest_result_payload(BLOCKED_KEY, include_report=True)

    assert payload["open_positions"][0]["strategy"] == "s1"
    assert payload["open_positions"][0]["strategy_name"] == "测试策略"


def test_report_strategy_name_falls_back_to_id_when_unregistered(tmp_path, monkeypatch):
    """策略可能已从注册表删除：查不到名字就留 None，让前端回落到 id，不能报错。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    payload = backtest_result_payload(BLOCKED_KEY, include_report=True)

    assert payload["result"]["summary"][0]["strategy_name"] is None
    assert payload["result"]["strategies"] == ["s1"]
    assert payload["open_positions"][0]["strategy_name"] is None


# ---------------------------------------------------------------------------
# 决策④：参数快照显示「实际跑了什么」而不是「请求了什么」
# ---------------------------------------------------------------------------


def _insert_job(tmp_path, job_id: str, request: dict) -> None:
    from trendradar.infrastructure.storage.connection import StorageConnection

    conn = StorageConnection(tmp_path / "storage").connect()
    conn.execute(
        "INSERT OR REPLACE INTO jobs (job_id, job_type, status, request_json) "
        "VALUES (?, ?, ?, ?)",
        (job_id, "backtest", "success", json.dumps(request)),
    )
    conn.commit()
    conn.close()


def test_report_trade_rule_shows_effective_defaults(tmp_path, monkeypatch):
    """原来 trade_rule 只装请求里的覆盖项，默认持有天数压根不在里面，
    前端只能说「按当前交易规则执行」——报告得说清这次到底按什么规则跑的。
    生效配置随产物固化后，持有天数取实跑值（快照），其余字段仍是 dataclass 默认。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    rule = backtest_result_payload(BLOCKED_KEY)["trade_rule"]

    # 快照记录实跑的 1（_run_blocked_backtest 的 config），不再是读取时重算的默认 5
    assert rule["fixed_hold_n_days"] == 1
    assert rule["entry_on_signal_day"] is False
    assert rule["entry_at_close"] is False
    assert rule["reject_if_limit_up_on_buy"] is True
    assert rule["postpone_if_limit_down_on_sell"] is True
    assert rule["force_sell_on_two_day_close_below_long_term_bull_bear_line"] is False
    assert rule["close_below_recent_low_stop_window"] is None
    assert rule["lot_size"] == 100
    assert rule["costs"]["commission_rate"] == pytest.approx(0.0003)
    assert rule["costs"]["slippage_buy_bp"] == 2.0
    assert rule["costs"]["slippage_sell_bp"] == 2.0
    assert "stamp_duty_rate_sell" in rule["costs"]  # 取值由 test_backtest_cost_defaults 钉住
    # 前端不暴露这些参数，默认全是"不限"，但报告得能说出来这次是无限的
    assert rule["position_limits"] == {
        "target_positions": None, "max_positions": None,
        "max_single_position_pct": None, "max_daily_new_positions": None,
    }


def test_report_trade_rule_reflects_overrides_and_nested_shape(tmp_path, monkeypatch):
    """覆盖项以实跑快照为准；trade_strategy 仍取自请求（请求数据，不随默认值漂移）。
    selection_backtest 的嵌套 capital 由 _backtest_config 另读，见 test_backtest_config_reads_nested_capital。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    key = "job_ultra"
    _run_backtest_to_disk(
        tmp_path, monkeypatch, key, _make_market(days),
        BacktestConfig(
            capital=CapitalConfig(mode="realistic", fixed_cash_per_trade=80000),
            execution=ExecutionConfig(
                fixed_hold_n_days=1, entry_on_signal_day=True, entry_at_close=True,
            ),
            portfolio=PortfolioConfig(max_positions=20, max_daily_new_positions=5),
        ),
    )
    _insert_job(tmp_path, key, {
        "backtest": {
            "capital": {"mode": "realistic", "fixed_cash_per_trade": 80000},
            "execution": {
                "fixed_hold_n_days": 1,
                "entry_on_signal_day": True,
                "entry_at_close": True,
            },
            "portfolio": {"max_positions": 20, "max_daily_new_positions": 5},
        },
        "trade_strategy": "ultra_short",
    })

    rule = backtest_result_payload(key)["trade_rule"]
    assert rule["fixed_hold_n_days"] == 1
    assert rule["entry_on_signal_day"] is True
    assert rule["entry_at_close"] is True
    assert rule["reject_if_limit_up_on_buy"] is True   # 未覆盖的仍是默认
    assert rule["trade_strategy"] == "ultra_short"
    assert rule["position_limits"] == {
        "target_positions": None, "max_positions": 20,
        "max_single_position_pct": None, "max_daily_new_positions": 5,
    }


def test_worker_persists_effective_config_snapshot(tmp_path, monkeypatch):
    """生效配置必须随产物固化：读取时重算依赖会漂移的默认值（P1#2 根因）。"""
    from dataclasses import asdict

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    config = BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=3))
    bt_dir, _ = _run_backtest_to_disk(
        tmp_path, monkeypatch, "bt-snap-test", _make_market(days), config,
    )

    snap = json.loads((bt_dir / "effective_config.json").read_text(encoding="utf-8"))
    assert snap == asdict(config)
    assert snap["execution"]["fixed_hold_n_days"] == 3


def test_report_trade_rule_prefers_snapshot_over_rebuild(tmp_path, monkeypatch):
    """报告优先读快照：实跑 hold=1，jobs 行重算会吃当前默认 5，必须显示真相 1。

    这正是 P1#2 的失真点——默认值一改，读取时重算就让历史报告集体改口，
    和自己的逐笔盈亏对不上。快照锚定实跑值，重算不再有机会覆盖它。
    """
    from trendradar.interfaces.api.presenters import backtest_result_payload

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    key = "bt-snap-beats-rebuild"
    _run_backtest_to_disk(
        tmp_path, monkeypatch, key, _make_market(days),
        BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
    )
    _insert_job(tmp_path, key, {"execution": {}})   # 未覆盖 hold → 重算会得默认 5

    rule = backtest_result_payload(key)["trade_rule"]
    assert rule["fixed_hold_n_days"] == 1


def test_report_trade_rule_falls_back_to_rebuild_without_snapshot(tmp_path, monkeypatch):
    """旧产物没有快照：回落读取时重算，不劣于现状（默认值照填，报告不空白）。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(8)]
    key = "bt-no-snapshot"
    bt_dir, _ = _run_backtest_to_disk(
        tmp_path, monkeypatch, key, _make_market(days),
        BacktestConfig(execution=ExecutionConfig(fixed_hold_n_days=1)),
    )
    (bt_dir / "effective_config.json").unlink()      # 模拟快照机制上线前的历史产物
    _insert_job(tmp_path, key, {"execution": {}})

    rule = backtest_result_payload(key)["trade_rule"]
    assert rule["fixed_hold_n_days"] == 5            # 重算吃当前默认


# ---------------------------------------------------------------------------
# 纯 bug ①：报告页「选股日期」「策略快照」原来是写死的空值
# ---------------------------------------------------------------------------


def _write_selection_artifacts(
    tmp_path, sel_key: str, *, signal_from: str, signal_to: str, strategies: list[dict],
) -> None:
    d = (
        tmp_path / "storage" / "objects" / "executions" / sel_key / "selection"
    )
    d.mkdir(parents=True, exist_ok=True)
    (d / "signals.json").write_text(json.dumps(
        {"signals": [], "signal_from": signal_from, "signal_to": signal_to}
    ), encoding="utf-8")
    (d / "lineage.json").write_text(
        json.dumps({"resolved_strategies": strategies}), encoding="utf-8"
    )
    (d / "manifest.json").write_text(
        json.dumps({"execution_key": sel_key, "status": "success"}), encoding="utf-8"
    )


def _link_selection(tmp_path, sel_key: str, bt_key: str) -> None:
    from trendradar.infrastructure.storage.connection import StorageConnection

    conn = StorageConnection(tmp_path / "storage").connect()
    for key, etype in ((sel_key, "selection"), (bt_key, "backtest")):
        conn.execute(
            "INSERT OR IGNORE INTO executions (execution_key, execution_type) VALUES (?, ?)",
            (key, etype),
        )
    conn.execute(
        "INSERT OR IGNORE INTO execution_links "
        "(source_execution_key, target_execution_key, link_type) VALUES (?, ?, ?)",
        (sel_key, bt_key, "backtest_uses_selection"),
    )
    conn.commit()
    conn.close()


_SNAP = [{"strategy_id": "s1", "name": "测试策略", "params": {"j_threshold": 20}}]


def test_report_reads_source_selection_window_and_snapshots(tmp_path, monkeypatch):
    """backtest_from_selection 的报告：来源选股已经落在自己目录里，数据齐全，
    presenter 却把 selection_from/selection_to 写死 None、strategy_snapshots 写死 []，
    于是「选股日期」整行不渲染、策略快照是空卡。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    _write_selection_artifacts(
        tmp_path, "sel-src",
        signal_from="2026-08-10", signal_to="2026-08-20", strategies=_SNAP,
    )
    _link_selection(tmp_path, "sel-src", BLOCKED_KEY)

    with _strategy_s1_registered():
        run = backtest_result_payload(BLOCKED_KEY)

    assert run["selection_execution_keys"] == ["sel-src"]
    assert run["selection_from"] == "2026-08-10"
    assert run["selection_to"] == "2026-08-20"
    assert run["strategy_snapshots"][0]["name"] == "测试策略"
    assert run["strategy_snapshots"][0]["class"] == "Fake"
    assert run["strategy_snapshots"][0]["params"] == {"j_threshold": 20}


def test_report_merges_multiple_source_selections(tmp_path, monkeypatch):
    """一次回测可以取多个选股区间的信号：日期取并集的首尾，快照按策略名去重。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    _write_selection_artifacts(
        tmp_path, "sel-a",
        signal_from="2026-08-10", signal_to="2026-08-12", strategies=_SNAP,
    )
    _write_selection_artifacts(
        tmp_path, "sel-b",
        signal_from="2026-07-01", signal_to="2026-08-20",
        strategies=[{"strategy_id": "s2", "name": "另一个策略", "params": {}}],
    )
    _link_selection(tmp_path, "sel-a", BLOCKED_KEY)
    _link_selection(tmp_path, "sel-b", BLOCKED_KEY)

    run = backtest_result_payload(BLOCKED_KEY)

    assert run["selection_from"] == "2026-07-01"
    assert run["selection_to"] == "2026-08-20"
    assert [s["name"] for s in run["strategy_snapshots"]] == ["测试策略", "另一个策略"]


def test_report_shows_own_selection_for_combined_pipeline(tmp_path, monkeypatch):
    """selection_backtest 里选股和回测共用 job_id，产物在同一目录的两个子目录下，
    没有任何血缘链接——不能因为查不到 link 就显示空值。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    _write_selection_artifacts(
        tmp_path, BLOCKED_KEY,
        signal_from="2026-08-10", signal_to="2026-08-18", strategies=_SNAP,
    )

    run = backtest_result_payload(BLOCKED_KEY)

    assert run["selection_execution_keys"] == []
    assert run["selection_from"] == "2026-08-10"
    assert run["selection_to"] == "2026-08-18"
    assert [s["name"] for s in run["strategy_snapshots"]] == ["测试策略"]


def test_report_without_selection_stays_empty(tmp_path, monkeypatch):
    """直接喂信号的 backtest_from_selection 找不到来源选股时，仍是空值而不是报错。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)

    run = backtest_result_payload(BLOCKED_KEY)

    assert run["selection_from"] is None
    assert run["selection_to"] is None
    assert run["strategy_snapshots"] == []


# ---------------------------------------------------------------------------
# 纯 bug ③：created_at == finished_at（都取目录 mtime）
# ---------------------------------------------------------------------------


def _insert_job_with_times(
    tmp_path, job_id: str, *, created_at: str, finished_at: str, status: str = "success"
) -> None:
    from trendradar.infrastructure.storage.connection import StorageConnection

    conn = StorageConnection(tmp_path / "storage").connect()
    conn.execute(
        "INSERT OR REPLACE INTO jobs "
        "(job_id, job_type, status, request_json, created_at, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, "backtest", status, "{}", created_at, finished_at),
    )
    conn.commit()
    conn.close()


def test_report_timestamps_come_from_jobs_table(tmp_path, monkeypatch):
    """原来两个字段都取产物目录 mtime，实跑里 6 分 46 秒的回测显示成 0 秒；
    jobs 表里 created 06:41:20 / finished 06:48:06 就是真数据。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    _insert_job_with_times(
        tmp_path, BLOCKED_KEY,
        created_at="2026-09-04 06:41:20",   # SQLite CURRENT_TIMESTAMP：空格分隔、无时区
        finished_at="2026-09-04T06:48:06",  # 代码写入：T 分隔、同样无时区但为 UTC
    )

    run = backtest_result_payload(BLOCKED_KEY)

    # 补 +00:00 是必须的：裸字符串会被 dayjs 当本地时间解析，非 UTC 用户看到的时间会错
    assert run["created_at"] == "2026-09-04T06:41:20+00:00"
    assert run["finished_at"] == "2026-09-04T06:48:06+00:00"


def test_report_falls_back_to_mtime_when_job_missing(tmp_path, monkeypatch):
    """历史产物可能没有 jobs 行（或还没跑完没有 finished_at）：回落到目录 mtime，
    不能因为查不到就报错或显示空白。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    run = backtest_result_payload(BLOCKED_KEY)

    assert run["created_at"] == run["finished_at"]
    assert run["created_at"] is not None
    assert "+" in run["created_at"] or run["created_at"].endswith("Z")


# ---------------------------------------------------------------------------
# 纯 bug ⑧：报告 status 写死 "success"，不看 jobs 表真实状态
# ---------------------------------------------------------------------------


def test_report_status_comes_from_jobs_table(tmp_path, monkeypatch):
    """jobs 表里有真实状态（queued/running/failed），payload 写死 success 是假数据：
    失败/进行中的作业被报告页显示成成功。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    _insert_job_with_times(
        tmp_path, BLOCKED_KEY,
        created_at="2026-09-04 06:41:20",
        finished_at="2026-09-04T06:48:06",
        status="failed",
    )

    run = backtest_result_payload(BLOCKED_KEY)

    assert run["status"] == "failed"


def test_report_status_falls_back_to_success_when_job_missing(tmp_path, monkeypatch):
    """历史产物没有 jobs 行：产物存在即视为跑完（result.json 只在结束时写），
    回落 success，与 ③ 的 mtime 回落同一逻辑，不因查不到而报错。"""
    from trendradar.interfaces.api.presenters import backtest_result_payload

    _run_blocked_backtest(tmp_path, monkeypatch)
    run = backtest_result_payload(BLOCKED_KEY)

    assert run["status"] == "success"
