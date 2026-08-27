"""API contract tests: response shapes must match the frontend type definitions.

The frontend (frontend/src/types/*.ts and services/*.ts) consumes:
- bare responses (no {"data": ...} wrapper)
- rich aggregated result objects (summary, strategies, dates, ...)

These tests pin that contract so the Web workbench renders real data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


# ---------------------------------------------------------------------------
# fixtures: a realistic V2 storage tree
# ---------------------------------------------------------------------------


def _write_selection_artifacts(storage: Path, key: str, signal_date: str = "2026-08-20") -> None:
    sel_dir = storage / "objects" / "executions" / key / "selection"
    sel_dir.mkdir(parents=True)
    (sel_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "execution_key": key,
                "execution_type": "selection",
                "created_at": "2026-08-20T10:00:00+00:00",
                "status": "success",
            }
        ),
        encoding="utf-8",
    )
    (sel_dir / "lineage.json").write_text(
        json.dumps(
            {
                "resolved_strategies": [
                    {"strategy_id": "bbi_kdj_b1", "name": "B1战法", "params": {}},
                    {"strategy_id": "peak_kdj", "name": "填坑战法", "params": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (sel_dir / "signals.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "execution_key": key,
                "signal_from": signal_date,
                "signal_to": signal_date,
                "strategies_snapshot": [
                    {"strategy_id": "bbi_kdj_b1", "name": "B1战法", "params": {}},
                    {"strategy_id": "peak_kdj", "name": "填坑战法", "params": {}},
                ],
                "strategy_groups_snapshot": [],
                "signals": [
                    {
                        "strategy_id": "bbi_kdj_b1",
                        "strategy_name": "B1战法",
                        "group_ids": ["default"],
                        "primary_group_id": "default",
                        "signal_date": signal_date,
                        "codes": ["000001", "600519"],
                    },
                    {
                        "strategy_id": "peak_kdj",
                        "strategy_name": "填坑战法",
                        "group_ids": ["default"],
                        "primary_group_id": "default",
                        "signal_date": signal_date,
                        "codes": ["000333"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_backtest_artifacts(storage: Path, key: str) -> None:
    bt_dir = storage / "objects" / "executions" / key / "backtest"
    bt_dir.mkdir(parents=True)
    trades = [
        {
            "strategy": "bbi_kdj_b1",
            "code": "000001",
            "signal_date": "2026-08-10",
            "buy_date": "2026-08-11",
            "sell_date": "2026-08-18",
            "buy_price": 10.0,
            "sell_price": 10.8,
            "shares": 4000,
            "profit": 3100.0,
            "return_pct": 7.8,
            "sell_postpone_days": 0,
        },
        {
            "strategy": "peak_kdj",
            "code": "000333",
            "signal_date": "2026-08-10",
            "buy_date": "2026-08-11",
            "sell_date": "2026-08-18",
            "buy_price": 50.0,
            "sell_price": 48.0,
            "shares": 900,
            "profit": -1900.0,
            "return_pct": -4.0,
            "sell_postpone_days": 1,
        },
    ]
    skips = [
        {
            "strategy": "bbi_kdj_b1",
            "code": "600519",
            "signal_date": "2026-08-10",
            "buy_date": "2026-08-11",
            "stage": "buy",
            "reason": "涨停无法买入",
        }
    ]
    equity = [
        {"date": "2026-08-11", "cash": 950000.0, "equity": 1000000.0, "position_count": 2},
        {"date": "2026-08-18", "cash": 951200.0, "equity": 1011200.0, "position_count": 0},
    ]
    result = {
        "metrics": {
            "trade_count": 2,
            "total_return_pct": 1.2,
            "win_rate_pct": 50.0,
            "sharpe": 0.8,
            "max_drawdown_pct": -1.0,
        },
        "trade_count": 2,
        "skip_count": 1,
        "trades": trades,
        "skips": skips,
        "equity_curve": equity,
    }
    (bt_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    (bt_dir / "metrics.json").write_text(
        json.dumps(result["metrics"]), encoding="utf-8"
    )


def _write_bars(storage: Path) -> None:
    bars_dir = storage / "market" / "bars"
    bars_dir.mkdir(parents=True)
    rows = []
    for i in range(13):  # 2026-08-18 .. 2026-08-30
        d = date(2026, 8, 18) + timedelta(days=i)
        rows.append(
            {
                "code": "000001",
                "date": d,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.4,
                "volume": 1000000.0,
                "amount": 10400000.0,
                "adj_factor": 1.0,
                "is_suspended": False,
            }
        )
    pl.DataFrame(rows, schema_overrides={"date": pl.Date}).write_parquet(
        bars_dir / "000001.parquet"
    )


def _write_stock_meta(storage: Path) -> None:
    meta = pl.DataFrame(
        {
            "ts_code": ["000001.SZ", "600519.SH", "000333.SZ"],
            "code": ["000001", "600519", "000333"],
            "name": ["平安银行", "贵州茅台", "美的集团"],
            "industry": ["银行", "白酒", "家用电器"],
        }
    )
    meta.write_parquet(storage / "market" / "stock_meta.parquet")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir(parents=True)

    init_schema(StorageConnection(storage).connect())

    _write_selection_artifacts(storage, "20260820_100000_selection_a1b2", signal_date="2026-08-30")
    _write_selection_artifacts(storage, "20260820_100000_selection_a2b3", signal_date="2026-08-20")
    _write_backtest_artifacts(storage, "20260820_100100_backtest_c3d4")
    _write_bars(storage)
    _write_stock_meta(storage)

    monkeypatch.setenv("TREND_RADAR_FRONTEND_DIST", str(tmp_path / "missing-dist"))

    from fastapi.testclient import TestClient
    from trendradar.interfaces.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _selection_result_payload(client, key: str) -> dict:
    resp = client.get(f"/api/selection-results/{key}")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------


def test_strategies_response_shape(client):
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, dict)
    assert "strategies" in payload
    assert len(payload["strategies"]) == 14
    for s in payload["strategies"]:
        assert {"name", "class", "description", "params"} <= set(s)


# ---------------------------------------------------------------------------
# selection results
# ---------------------------------------------------------------------------


def test_selection_results_list_shape(client):
    resp = client.get("/api/selection-results")
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, dict)
    assert "results" in payload
    runs = payload["results"]
    assert len(runs) >= 1
    run = next(r for r in runs if r["execution_key"] == "20260820_100000_selection_a1b2")
    assert run["status"] == "success"
    assert run["selection_from"] == "2026-08-30"
    assert run["selection_to"] == "2026-08-30"
    assert set(run["strategies"]) == {"B1战法", "填坑战法"}
    assert isinstance(run["strategy_snapshots"], list)
    assert run["summary"][0]["strategy"] == "B1战法"
    assert run["summary"][0]["count"] == 2
    assert run["summary"][0]["date"] == "2026-08-30"


def test_selection_result_detail_shape(client):
    payload = _selection_result_payload(client, "20260820_100000_selection_a1b2")
    assert set(payload) == {"result", "artifacts", "picks"}
    result = payload["result"]
    assert result["execution_key"] == "20260820_100000_selection_a1b2"
    assert result["summary"][1]["strategy"] == "填坑战法"
    assert result["summary"][1]["count"] == 1
    assert isinstance(payload["artifacts"], list)
    assert len(payload["artifacts"]) == 3
    picks = payload["picks"]
    assert len(picks) == 3
    for pick in picks:
        assert {"execution_key", "strategy", "date", "code"} <= set(pick)
    assert picks[0]["strategy"] == "B1战法"
    assert picks[0]["code"] == "000001"
    # stock names and industries come from stock_meta.parquet
    assert picks[0]["name"] == "平安银行"
    assert picks[0]["industry"] == "银行"
    by_code = {p["code"]: p for p in picks}
    assert by_code["600519"]["name"] == "贵州茅台"
    assert by_code["600519"]["industry"] == "白酒"
    assert by_code["000333"]["name"] == "美的集团"
    assert by_code["000333"]["industry"] == "家用电器"


def test_selection_result_not_found(client):
    resp = client.get("/api/selection-results/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# list pagination (?limit=) — frontend sends limit=50; backend must honor it
# ---------------------------------------------------------------------------


def _seed_extra_selection(client) -> None:
    from trendradar.infrastructure.runtime import runtime_root

    _write_selection_artifacts(
        runtime_root() / "storage", "20260820_100000_selection_b0c1", signal_date="2026-08-21"
    )


def _seed_extra_backtest(client) -> None:
    from trendradar.infrastructure.runtime import runtime_root

    _write_backtest_artifacts(
        runtime_root() / "storage", "20260820_100100_backtest_e5f6"
    )


def test_selection_results_limit(client):
    _seed_extra_selection(client)
    full = client.get("/api/selection-results").json()["results"]
    assert len(full) == 3  # sanity: fixture 2 + seeded 1
    keys = [r["execution_key"] for r in full]
    assert keys == sorted(keys, reverse=True)  # newest-first ordering

    limited = client.get("/api/selection-results?limit=2").json()["results"]
    assert [r["execution_key"] for r in limited] == keys[:2]


def test_backtest_results_limit(client):
    _seed_extra_backtest(client)
    full = client.get("/api/backtest-results").json()["results"]
    assert len(full) == 2
    keys = [r["execution_key"] for r in full]

    limited = client.get("/api/backtest-results?limit=1").json()["results"]
    assert [r["execution_key"] for r in limited] == keys[:1]


@pytest.mark.parametrize("bad", ["abc", "-1", "0"])
def test_results_list_invalid_limit_returns_422(client, bad):
    assert client.get(f"/api/selection-results?limit={bad}").status_code == 422
    assert client.get(f"/api/backtest-results?limit={bad}").status_code == 422


# ---------------------------------------------------------------------------
# backtest results
# ---------------------------------------------------------------------------


def test_backtest_results_list_shape(client):
    resp = client.get("/api/backtest-results")
    assert resp.status_code == 200
    payload = resp.json()
    assert "results" in payload
    runs = payload["results"]
    assert len(runs) >= 1
    run = runs[0]
    assert run["execution_key"] == "20260820_100100_backtest_c3d4"
    assert run["status"] == "success"
    assert run["start_date"] == "2026-08-11"
    assert run["end_date"] == "2026-08-18"
    assert set(run["strategies"]) == {"bbi_kdj_b1", "peak_kdj"}
    assert run["capital_mode"] in ("unlimited_cash", "realistic")
    summaries = {s["strategy"]: s for s in run["summary"]}
    assert "bbi_kdj_b1" in summaries
    assert summaries["bbi_kdj_b1"]["trade_count"] == 1
    assert summaries["bbi_kdj_b1"]["skip_count"] == 1
    assert summaries["bbi_kdj_b1"]["win_rate_pct"] == 100.0
    assert summaries["peak_kdj"]["trade_count"] == 1
    assert summaries["peak_kdj"]["win_rate_pct"] == 0.0


def test_backtest_report_shape(client):
    resp = client.get("/api/backtest-results/20260820_100100_backtest_c3d4/report")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) == {"result", "artifacts", "trades", "equity", "skips"}
    assert payload["result"]["execution_key"] == "20260820_100100_backtest_c3d4"
    assert len(payload["trades"]) == 2
    assert payload["trades"][0]["strategy"] == "bbi_kdj_b1"
    assert len(payload["equity"]) == 2
    assert len(payload["skips"]) == 1
    assert len(payload["artifacts"]) >= 2


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------


def test_trading_dates_shape(client):
    resp = client.get("/api/market-data/trading-dates")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"from", "to", "count", "dates"}
    assert payload["count"] == 13
    assert payload["dates"][0] == "2026-08-18"
    assert payload["dates"][-1] == "2026-08-30"


def test_market_status_shape(client):
    resp = client.get("/api/market-data/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"data_dir", "stocklist", "stock_count", "local_file_count", "latest_date"}
    assert payload["stock_count"] == 1
    assert payload["local_file_count"] == 1
    assert payload["latest_date"] == "2026-08-30"


# ---------------------------------------------------------------------------
# execution submission + console
# ---------------------------------------------------------------------------


def test_submit_execution_returns_console_url(client):
    resp = client.post(
        "/api/executions",
        json={"type": "selection_single", "params": {"date": "2026-08-20"}},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_id"]
    assert payload["status"] == "submitted"
    assert payload["execution_type"] == "selection"
    assert payload["console_url"].startswith("/console/")


def test_submit_execution_accepts_flat_request(client):
    resp = client.post(
        "/api/executions",
        json={"start_date": "2026-08-18", "end_date": "2026-08-19", "strategies": ["B1战法"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_id"]


def test_console_response_shape(client):
    resp = client.post(
        "/api/executions",
        json={"type": "selection_single", "params": {"date": "2026-08-20"}},
    )
    job_id = resp.json()["execution_id"]

    console_resp = client.get(f"/api/executions/{job_id}/console")
    assert console_resp.status_code == 200
    payload = console_resp.json()
    assert set(payload) == {"execution", "offset", "text", "more"}
    execution = payload["execution"]
    assert execution["execution_id"] == job_id
    assert execution["execution_type"] == "selection"
    assert execution["status"] in ("queued", "running", "success", "failed")
    assert "offset" in payload
    assert isinstance(payload["text"], str)
    assert isinstance(payload["more"], bool)
    # 每条日志独立成行：前端轮询依赖尾随换行，缺失会把相邻两条日志粘成一行
    if payload["text"]:
        assert payload["text"].endswith("\n")


# ---------------------------------------------------------------------------
# backtest submission prerequisites
# ---------------------------------------------------------------------------


def test_backtest_submit_blocked_when_signal_at_data_end(client):
    """Signals on the last trading day have no T+1 — must be rejected up front."""
    resp = client.post(
        "/api/backtests",
        json={"execution_key": "20260820_100000_selection_a1b2"},  # signal 08-30 = last day
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "无足够交易日" in detail
    assert "08-30" in detail


def test_backtest_submit_passes_when_signal_has_future_days(client):
    resp = client.post(
        "/api/backtests",
        json={"execution_key": "20260820_100000_selection_a2b3"},  # signal 08-20, 10 days left
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_id"]
    assert payload["console_url"].startswith("/console/")


# ---------------------------------------------------------------------------
# bulk delete
# ---------------------------------------------------------------------------


def test_bulk_delete_selection_results(client, tmp_path):
    resp = client.request(
        "DELETE",
        "/api/selection-results",
        json={"execution_keys": ["20260820_100000_selection_a1b2"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"result_type", "requested", "deleted", "missing", "file_errors"}
    assert payload["requested"] == 1
    assert payload["deleted"] == 1

    # file gone
    assert not (
        tmp_path / "storage" / "objects" / "executions" / "20260820_100000_selection_a1b2" / "selection" / "signals.json"
    ).exists()


def test_delete_selection_blocked_by_backtest_reference(client, tmp_path):
    import sqlite3

    db = tmp_path / "storage" / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
        ("20260820_100000_selection_a2b3", "selection"),
    )
    conn.execute(
        "INSERT INTO execution_links (source_execution_key, target_execution_key, link_type) "
        "VALUES (?, ?, 'backtest_uses_selection')",
        ("20260820_100000_selection_a2b3", "bt_xyz"),
    )
    conn.commit()
    conn.close()

    resp = client.request(
        "DELETE",
        "/api/selection-results",
        json={"execution_keys": ["20260820_100000_selection_a2b3"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["deleted"] == 0
    assert payload["file_errors"], "referenced selection must be reported in file_errors"
    assert (
        tmp_path / "storage" / "objects" / "executions" / "20260820_100000_selection_a2b3"
    ).is_dir()


def test_delete_backtest_result_rejects_dot(client, tmp_path):
    """'.' must not resolve to the executions root and wipe every result.

    The literal "/." is normalized away by HTTP clients (httpx rewrites it to
    "/"), so probe with the percent-encoded form that reaches the single-key
    route with execution_key == ".".
    """
    resp = client.request("DELETE", "/api/backtest-results/%2E")
    assert resp.status_code == 400
    root = tmp_path / "storage" / "objects" / "executions"
    assert (root / "20260820_100100_backtest_c3d4").is_dir()
    assert (root / "20260820_100000_selection_a1b2").is_dir()


def test_delete_backtest_result_rejects_non_backtest_dir(client, tmp_path):
    """Only directories confirmed to hold a backtest may be deleted."""
    resp = client.request("DELETE", "/api/backtest-results/20260820_100000_selection_a1b2")
    assert resp.status_code == 404
    assert (tmp_path / "storage" / "objects" / "executions" / "20260820_100000_selection_a1b2").is_dir()


def test_bulk_delete_backtest_results_respects_keys(client, tmp_path):
    """DELETE /backtest-results with execution_keys deletes only those, in the
    same contract shape as selection deletes."""
    resp = client.request(
        "DELETE",
        "/api/backtest-results",
        json={"execution_keys": ["20260820_100100_backtest_c3d4", "nonexistent_key"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"result_type", "requested", "deleted", "missing", "file_errors"}
    assert payload["result_type"] == "backtest"
    assert payload["requested"] == 2
    assert payload["deleted"] == 1
    assert payload["missing"] == ["nonexistent_key"]
    root = tmp_path / "storage" / "objects" / "executions"
    assert not (root / "20260820_100100_backtest_c3d4").exists()
    assert (root / "20260820_100000_selection_a1b2").is_dir()


def test_bulk_delete_backtest_results_no_body_deletes_all(client, tmp_path):
    resp = client.request("DELETE", "/api/backtest-results")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["result_type"] == "backtest"
    assert payload["deleted"] == 1
    assert not (tmp_path / "storage" / "objects" / "executions" / "20260820_100100_backtest_c3d4").exists()
    assert (tmp_path / "storage" / "objects" / "executions" / "20260820_100000_selection_a1b2").is_dir()


def test_market_sync_force_passthrough(client):
    resp = client.post(
        "/api/market-data/sync",
        json={"start_date": "2026-08-18", "end_date": "2026-08-19", "force": True, "codes": ["000001"]},
    )
    # force is accepted by the schema (200 even though sync job will fail on
    # missing token in tests; a job id is still produced)
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"]


def test_ultra_short_trade_strategy_maps_to_execution():
    from trendradar.interfaces.api.presenters import _trade_strategy_execution
    assert _trade_strategy_execution("ultra_short") == {
        "entry_on_signal_day": True, "entry_at_close": True, "fixed_hold_n_days": 1,
    }
    assert _trade_strategy_execution("long_term_bull_bear_stop") == {
        "force_sell_on_two_day_close_below_long_term_bull_bear_line": True,
    }
    assert _trade_strategy_execution("ten_day_low_stop") == {
        "close_below_recent_low_stop_window": 10,
    }
    assert _trade_strategy_execution(None) == {}


def test_backtest_from_selection_injects_ultra_short_execution(monkeypatch):
    from trendradar.app.services import backtest_service as bs
    from trendradar.domain.signal.models import SignalSet
    from trendradar.interfaces.api import presenters as p

    captured = {}

    def fake_submit(executor, market_store, repo, request):
        captured.update(request)
        return "job_x"

    monkeypatch.setattr(bs, "submit_backtest", fake_submit)
    monkeypatch.setattr(bs, "validate_backtest_prerequisites", lambda *a, **k: [])
    monkeypatch.setattr(p, "trading_dates_payload", lambda: {"dates": []})
    fake_repo = type("R", (), {"load": lambda self, k: SignalSet()})()
    # dispatch 内部用真实 SignalRepository——patch 其类以注入 fake repo
    monkeypatch.setattr(
        "trendradar.domain.signal.repository.SignalRepository",
        lambda store: fake_repo,
    )

    p.submit_execution_payload(None, None, None, {
        "type": "backtest_from_selection",
        "params": {
            "selection_execution_keys": ["k1"],
            "trade_strategy": "ultra_short",
            "mode": "unlimited_cash",
            "cash_per_trade": 50000,
        },
    })
    assert captured["execution"] == {
        "entry_on_signal_day": True, "entry_at_close": True, "fixed_hold_n_days": 1,
    }


def test_enrich_stock_info_adds_name_and_industry():
    from trendradar.interfaces.api.presenters import _enrich_stock_info
    trades = [{"code": "000001"}, {"code": "999999"}]
    meta = {"000001": {"name": "平安银行", "industry": "银行"}}
    out = _enrich_stock_info(trades, meta)
    assert out[0]["name"] == "平安银行"
    assert out[0]["industry"] == "银行"
    # 缺失代码保持原样（无 name 键）
    assert "name" not in out[1]
