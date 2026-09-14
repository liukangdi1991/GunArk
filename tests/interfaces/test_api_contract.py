"""API contract tests: response shapes must match the frontend type definitions.

The frontend (frontend/src/types/*.ts and services/*.ts) consumes:
- bare responses (no {"data": ...} wrapper)
- rich aggregated result objects (summary, strategies, dates, ...)

These tests pin that contract so the Web workbench renders real data.
"""

from __future__ import annotations

import json
import os
import time
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
                        "codes": ["000001", "000002", "600519"],
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
    open_positions = [
        {
            "execution_key": key,
            "strategy": "bbi_kdj_b1",
            "code": "000002",
            "signal_date": "2026-08-10",
            "buy_date": "2026-08-11",
            "target_sell_date": "2026-08-14",
            "shares": 4000,
            "entry_price": 10.0,
            "entry_cost": 40030.0,
            "mark_price": 7.29,
            "mark_date": "2026-08-18",
            "blocked_since": "2026-08-14",
            "blocked_reason": "跌停封死",
            "blocked_trading_days": 5,
            "unrealized_pnl": -10810.0,
            "unrealized_return_pct": -27.0,
        }
    ]
    result = {
        "metrics": {
            "trade_count": 2,
            "total_return_pct": 1.2,
            "win_rate_pct": 50.0,
            "sharpe": 0.8,
            "max_drawdown_pct": -1.0,
            "open_position_count": 1,
        },
        "trade_count": 2,
        "skip_count": 1,
        "trades": trades,
        "skips": skips,
        "open_positions": open_positions,
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
            "ts_code": ["000001.SZ", "600519.SH", "000333.SZ", "000002.SZ"],
            "code": ["000001", "600519", "000333", "000002"],
            "name": ["平安银行", "贵州茅台", "美的集团", "万科A"],
            "industry": ["银行", "白酒", "家用电器", "全国地产"],
            "market": ["主板", "主板", "主板", "主板"],
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
    assert run["summary"][0]["count"] == 3
    assert run["summary"][0]["date"] == "2026-08-30"


def test_selection_result_detail_shape(client):
    payload = _selection_result_payload(client, "20260820_100000_selection_a1b2")
    # P1：详情端点瘦身——picks 移到分页端点，改回 counts
    assert set(payload) == {"result", "artifacts", "counts"}
    result = payload["result"]
    assert result["execution_key"] == "20260820_100000_selection_a1b2"
    assert result["summary"][1]["strategy"] == "填坑战法"
    assert result["summary"][1]["count"] == 1
    assert isinstance(payload["artifacts"], list)
    assert len(payload["artifacts"]) == 3
    assert payload["counts"] == {"total_picks": 4, "unique_codes": 4, "trade_days": 1}


def test_selection_picks_page_contract(client):
    """分页契约：items/total/offset/limit；默认全序 strategy→date→code。"""
    key = "20260820_100000_selection_a1b2"
    resp = client.get(f"/api/selection-results/{key}/picks?limit=2&offset=0")
    assert resp.status_code == 200
    page = resp.json()
    assert set(page) == {"items", "total", "offset", "limit"}
    assert page["total"] == 4 and page["offset"] == 0 and page["limit"] == 2
    assert [p["code"] for p in page["items"]] == ["000001", "000002"]
    # 展示字段在分页端点补全（stock_meta 关联只作用于当页）
    assert page["items"][0]["name"] == "平安银行"
    assert page["items"][0]["industry"] == "银行"
    assert page["items"][1]["name"] == "万科A"

    tail = client.get(f"/api/selection-results/{key}/picks?limit=2&offset=2").json()
    assert [p["code"] for p in tail["items"]] == ["600519", "000333"]
    assert client.get(f"/api/selection-results/{key}/picks?offset=99").json()["items"] == []


def test_selection_picks_page_filters_and_sort(client):
    key = "20260820_100000_selection_a1b2"
    only_b1 = client.get(f"/api/selection-results/{key}/picks?strategies=B1战法").json()
    assert only_b1["total"] == 3
    both = client.get(f"/api/selection-results/{key}/picks?strategies=B1战法&strategies=填坑战法").json()
    assert both["total"] == 4
    day = client.get(f"/api/selection-results/{key}/picks?dates=2026-08-30").json()
    assert day["total"] == 4
    empty_day = client.get(f"/api/selection-results/{key}/picks?dates=1999-01-01").json()
    assert empty_day["total"] == 0 and empty_day["items"] == []
    desc = client.get(f"/api/selection-results/{key}/picks?sort_by=code&sort_dir=desc&limit=1").json()
    assert desc["items"][0]["code"] == "600519"


def test_selection_picks_page_rejects_bad_params(client):
    key = "20260820_100000_selection_a1b2"
    assert client.get(f"/api/selection-results/{key}/picks?sort_by=drop_table").status_code == 400
    assert client.get(f"/api/selection-results/{key}/picks?limit=0").status_code == 422
    assert client.get(f"/api/selection-results/{key}/picks?limit=201").status_code == 422
    assert client.get("/api/selection-results/nope/picks").status_code == 404
    assert client.get("/api/selection-results/..%2Fetc/picks").status_code in (400, 404)


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
    # strategies 是展示字段：中文名取自注册表；筛选键与 rowKey 用 summary[].strategy（id）
    assert set(run["strategies"]) == {"B1战法", "填坑战法"}
    assert run["capital_mode"] in ("unlimited_cash", "realistic")
    summaries = {s["strategy"]: s for s in run["summary"]}
    assert "bbi_kdj_b1" in summaries
    assert summaries["bbi_kdj_b1"]["strategy_name"] == "B1战法"
    assert summaries["bbi_kdj_b1"]["trade_count"] == 1
    assert summaries["bbi_kdj_b1"]["skip_count"] == 1
    assert summaries["bbi_kdj_b1"]["win_rate_pct"] == 100.0
    assert summaries["peak_kdj"]["trade_count"] == 1
    assert summaries["peak_kdj"]["win_rate_pct"] == 0.0
    assert summaries["bbi_kdj_b1"]["open_positions"] == 1
    assert summaries["peak_kdj"]["open_positions"] == 0


def test_backtest_report_shape(client):
    resp = client.get("/api/backtest-results/20260820_100100_backtest_c3d4/report")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) == {
        "result", "artifacts", "trades", "equity", "skips", "open_positions",
    }
    assert payload["result"]["execution_key"] == "20260820_100100_backtest_c3d4"
    assert len(payload["trades"]) == 2
    assert payload["trades"][0]["strategy"] == "bbi_kdj_b1"
    assert len(payload["equity"]) == 2
    assert len(payload["skips"]) == 1
    assert len(payload["artifacts"]) >= 2
    assert len(payload["open_positions"]) == 1
    assert payload["open_positions"][0]["blocked_reason"] == "跌停封死"


def test_backtest_report_conditional_get(client):
    """报告是产物级不变数据：带 ETag 的条件请求命中直接 304，不重建大响应体。"""
    key = "20260820_100100_backtest_c3d4"
    url = f"/api/backtest-results/{key}/report"

    first = client.get(url)
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag and etag.startswith('"') and etag.endswith('"')
    assert first.headers.get("cache-control") == "private, max-age=0, must-revalidate"

    hit = client.get(url, headers={"If-None-Match": etag})
    assert hit.status_code == 304
    assert hit.content == b""
    assert hit.headers.get("etag") == etag

    # 弱校验前缀与多值列表形态都要认（RFC 9110 的 If-None-Match 语义）
    weak = client.get(url, headers={"If-None-Match": f'W/{etag}, "other"'})
    assert weak.status_code == 304

    miss = client.get(url, headers={"If-None-Match": '"deadbeef-1"'})
    assert miss.status_code == 200
    assert miss.json()["result"]["execution_key"] == key


def test_backtest_report_etag_tracks_artifact_change(client, tmp_path):
    """产物被改写必须换 ETag，否则浏览器会一直拿旧报告。"""
    key = "20260820_100100_backtest_c3d4"
    url = f"/api/backtest-results/{key}/report"
    etag_before = client.get(url).headers["etag"]

    result_file = (
        tmp_path / "storage" / "objects" / "executions" / key / "backtest" / "result.json"
    )
    result_file.write_text(result_file.read_text(encoding="utf-8"), encoding="utf-8")
    future = time.time() + 10
    os.utime(result_file, (future, future))

    resp = client.get(url, headers={"If-None-Match": etag_before})
    assert resp.status_code == 200
    assert resp.headers["etag"] != etag_before


def test_backtest_report_is_gzipped(client):
    """大响应保持 gzip 传输（中间件档位调整不得关掉压缩）。"""
    resp = client.get(
        "/api/backtest-results/20260820_100100_backtest_c3d4/report",
        headers={"Accept-Encoding": "gzip"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert len(resp.content) < 10_000


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


def test_status_payload_has_six_groups(client):
    resp = client.get("/api/market-data/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"calendar", "freshness", "bars_sync", "coverage",
                            "consistency", "storage"}
    assert set(payload["calendar"]) == {"status", "max_trade_date", "covers_today", "job_id"}
    assert set(payload["freshness"]) == {"trusted_through", "latest_tradeable",
                                         "stale_days", "total_missing_days"}
    assert set(payload["bars_sync"]) == {"status", "finished_at", "error_message", "job_id"}
    assert set(payload["coverage"]) == {"missing_codes", "skipped"}
    assert set(payload["consistency"]) == {"ledger_suspect", "marker_mismatch",
                                           "doubtful_days"}
    # 旧平铺字段全部收进 storage 组（原 test_market_status_shape 的断言落点）
    assert payload["storage"]["stock_count"] == 1
    assert payload["storage"]["local_file_count"] == 1
    assert payload["storage"]["latest_date"] == "2026-08-30"
    # 新鲜度基于读回实测：fixture 只种了 000001 一只到 2026-08-30
    assert payload["freshness"]["trusted_through"] == "2026-08-30"


def test_market_status_readback_uses_cached_calendar(client, monkeypatch):
    """行情页冷路径不再全库扫描：走 mtime 校验的日历缓存（trading-dates 同款）。

    原实现每次冷路径都 readback_calendar() 重扫全部 bar 文件（实测 5,814 个
    parquet ≈ 0.9s，占冷路径九成）；改为复用 data_store 的日历缓存后，
    语义不变（同一份"全库日期并集"），成本降到 50ms 级。
    """
    import trendradar.domain.market.data_store as data_store_mod
    import trendradar.infrastructure.tushare.writer as writer_mod
    from trendradar.interfaces.api import presenters

    counts = {"readback": 0, "calendar": 0}
    real_readback = writer_mod.readback_calendar
    real_get_calendar = data_store_mod.LocalParquetMarketStore.get_calendar

    def spy_readback(bars_dir):
        counts["readback"] += 1
        return real_readback(bars_dir)

    def spy_get_calendar(self):
        counts["calendar"] += 1
        return real_get_calendar(self)

    monkeypatch.setattr(writer_mod, "readback_calendar", spy_readback)
    monkeypatch.setattr(
        data_store_mod.LocalParquetMarketStore, "get_calendar", spy_get_calendar
    )

    presenters.invalidate_market_status_cache()
    resp = client.get("/api/market-data/status")

    assert resp.status_code == 200
    assert counts["readback"] == 0, "status 冷路径不得全库扫描（readback_calendar）"
    assert counts["calendar"] >= 1, "status 必须走缓存日历"


def test_market_status_reflects_new_bars_after_cache(client, tmp_path):
    """新增行情文件后 status 必须反映新日期（缓存按文件 mtime 失效）。"""
    from trendradar.interfaces.api import presenters

    bars_dir = tmp_path / "storage" / "market" / "bars"
    assert client.get("/api/market-data/status").json()["storage"]["latest_date"] == "2026-08-30"

    pl.DataFrame(
        [
            {
                "code": "000002",
                "date": date(2026, 9, 1),
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "volume": 1.0, "amount": 1.0,
                "adj_factor": 1.0, "is_suspended": False,
            }
        ],
        schema_overrides={"date": pl.Date},
    ).write_parquet(bars_dir / "000002.parquet")
    # 明确推后 mtime：时间戳粒度粗的文件系统上避免"新文件不比缓存新"的假阴性
    future = time.time() + 10
    os.utime(bars_dir / "000002.parquet", (future, future))

    presenters.invalidate_market_status_cache()
    payload = client.get("/api/market-data/status").json()
    assert payload["storage"]["latest_date"] == "2026-09-01"
    assert payload["freshness"]["trusted_through"] == "2026-09-01"


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


def test_submit_selection_unknown_strategy_returns_400(client):
    """未知策略 id 是调用方错误，提交时就该 400，而不是静默吞掉后给个空结果。"""
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_single",
            "params": {"date": "2026-08-20", "strategies": ["bbi_kdj_b1", "no_such_strategy"]},
        },
    )
    assert resp.status_code == 400
    assert "no_such_strategy" in resp.json()["detail"]


def test_submit_selection_unknown_group_returns_400(client):
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_single",
            "params": {"date": "2026-08-20", "groups": ["ghost_group"]},
        },
    )
    assert resp.status_code == 400
    assert "ghost_group" in resp.json()["detail"]


def test_submit_selection_known_strategy_still_200(client):
    """校验不能误伤正常路径：已注册策略照常提交。"""
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_single",
            "params": {"date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["execution_id"]


def test_submit_selection_backtest_unknown_strategy_returns_400(client):
    """selection_backtest 走同一条校验：未知策略 id 提交时 400，不入队。"""
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_backtest",
            "params": {
                "from": "2026-08-18",
                "to": "2026-08-20",
                "strategies": ["bbi_kdj_b1", "no_such_strategy"],
            },
        },
    )
    assert resp.status_code == 400, resp.json()
    assert "no_such_strategy" in resp.json()["detail"]


def test_legacy_selection_backtest_route_unknown_strategy_returns_400(client):
    """POST /api/selection-backtest 是同类公开入口，未知策略 id 同样 400。"""
    resp = client.post(
        "/api/selection-backtest",
        json={
            "start_date": "2026-08-18",
            "end_date": "2026-08-20",
            "strategies": ["bbi_kdj_b1", "no_such_strategy"],
        },
    )
    assert resp.status_code == 400
    assert "no_such_strategy" in resp.json()["detail"]


def test_submit_selection_invalid_board_returns_400(client):
    """非法板块值提交时 400（校验在 enqueue 前），不是静默吞掉或作业失败。"""
    resp = client.post(
        "/api/executions",
        json={"type": "selection_single", "params": {"date": "2026-08-20", "boards": ["Hack"]}},
    )
    assert resp.status_code == 400
    assert "Hack" in resp.json()["detail"]


def test_selection_backtest_boards_passthrough(client, monkeypatch):
    """Critical 3 回归：selection_backtest 的 boards/codes 必须透传进请求（schema 不再吞）。"""
    captured = {}

    def fake_submit(executor, market_store, repo, request):
        captured.update(request)
        return "job-boards-1"

    import trendradar.app.services.backtest_service as bs
    monkeypatch.setattr(bs, "submit_selection_backtest", fake_submit)
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_backtest",
            "params": {
                "from": "2026-08-18", "to": "2026-08-20",
                "boards": ["北交所"], "codes": ["920001"],
                "strategies": ["bbi_kdj_b1"],
            },
        },
    )
    assert resp.status_code == 200
    assert captured["boards"] == ["北交所"]
    assert captured["codes"] == ["920001"]


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
    """引用保护（复审 E2E #2 更新契约）：被**仍存在**的回测引用 → 删除被拒。
    悬空引用（回测目录已删）不再阻断——见 test_delete_backtest_heals_dangling_selection_reference。"""
    import sqlite3

    bt_root = tmp_path / "storage" / "objects" / "executions" / "bt_xyz"
    (bt_root / "backtest").mkdir(parents=True)
    (bt_root / "backtest" / "metrics.json").write_text("{}")

    db = tmp_path / "storage" / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
        ("20260820_100000_selection_a2b3", "selection"),
    )
    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
        ("bt_xyz", "backtest"),
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


def test_delete_backtest_heals_dangling_selection_reference(client, tmp_path):
    """复审 E2E #2：删除回测后 links 残留不得永久锁死其选股——
    引用检查过滤已不存在的回测目录（自愈），且删回测时清理 links 行。"""
    import sqlite3

    db = tmp_path / "storage" / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
        ("20260820_100000_selection_a2b3", "selection"),
    )
    conn.execute(
        "INSERT INTO executions (execution_key, execution_type) VALUES (?, ?)",
        ("20260820_100100_backtest_c3d4", "backtest"),
    )
    conn.execute(
        "INSERT INTO execution_links (source_execution_key, target_execution_key, link_type) "
        "VALUES (?, ?, 'backtest_uses_selection')",
        ("20260820_100000_selection_a2b3", "20260820_100100_backtest_c3d4"),
    )
    conn.commit()
    conn.close()

    # 引用保护先生效：回测目录存在时，选股必须删不掉
    resp = client.request(
        "DELETE", "/api/selection-results",
        json={"execution_keys": ["20260820_100000_selection_a2b3"]},
    )
    assert resp.json()["deleted"] == 0

    # 删回测（真实删除目录 + 清理 links 行）
    resp = client.request(
        "DELETE", "/api/backtest-results",
        json={"execution_keys": ["20260820_100100_backtest_c3d4"]},
    )
    assert resp.json()["deleted"] == 1

    # 选股不再被悬空引用锁死 → 可删
    resp = client.request(
        "DELETE", "/api/selection-results",
        json={"execution_keys": ["20260820_100000_selection_a2b3"]},
    )
    assert resp.json()["deleted"] == 1
    assert not (tmp_path / "storage" / "objects" / "executions" / "20260820_100000_selection_a2b3").exists()


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


def test_market_bars_sync_submit_accepts_new_schema(client, monkeypatch):
    """路由接受新 schema 并把参数原样交给 executor。

    必须替身 executor：真作业会去拉真实 Tushare（凭证在本机 ~/tk.csv、网络可用时
    就是整市场全量同步，TestClient 关闭会一直等它跑完——整个测试文件随即挂死）。
    """
    captured: dict = {}

    def _capture(job_type, run_fn, request):
        captured.update(job_type=job_type, request=request)
        return "job-sync-test"

    monkeypatch.setattr(client.app.state.executor, "submit", _capture)
    resp = client.post(
        "/api/market-data/sync",
        json={"force": True, "exclude_boards": ["gem"], "accept_partial_baseline": True},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"] == "job-sync-test"
    assert captured["job_type"] == "market_bars_sync"
    assert captured["request"] == {
        "force": True,
        "exclude_boards": ["gem"],
        "accept_partial_baseline": True,
        "codes": None,
    }


def test_market_backfill_submit(client, monkeypatch):
    """同上：替身 executor，只验提交契约（真作业会打真实 Tushare）。"""
    captured: dict = {}

    def _capture(job_type, run_fn, request):
        captured.update(job_type=job_type, request=request)
        return "job-backfill-test"

    monkeypatch.setattr(client.app.state.executor, "submit", _capture)
    resp = client.post("/api/market-data/backfill", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"] == "job-backfill-test"
    assert captured["job_type"] == "market_backfill_codes"
    assert captured["request"] == {"codes": None, "exclude_boards": []}


def test_submit_execution_conflict_returns_409(client, monkeypatch):
    """主同步按钮走通用路由：互斥拒绝必须是 409，不能被吞成 500。"""
    from trendradar.app.jobs.executor import JobConflictError

    def _conflict(*args, **kwargs):
        raise JobConflictError("market_bars_sync job conflicts with an in-flight sync job")

    monkeypatch.setattr(client.app.state.executor, "submit", _conflict)
    resp = client.post("/api/executions", json={"type": "market_bars_sync", "params": {}})
    assert resp.status_code == 409
    assert "conflicts" in resp.json()["detail"]


def test_confirm_doubtful_noop_when_empty(client):
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 200
    assert resp.json() == {"status": "noop", "confirmed": []}


def test_confirm_doubtful_rejects_days_not_on_disk(client):
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from datetime import date

    # 2015-07-08 不在 fixture 种下的 08-18..08-30 范围内 → 不在盘上
    store = SyncStore(runtime_root() / "storage")
    store.set_doubtful_days([date(2015, 7, 8)])
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 400
    store2 = SyncStore(runtime_root() / "storage")
    assert store2.doubtful_days() == [date(2015, 7, 8)]         # 拒绝且不写账本


def test_confirm_doubtful_books_when_on_disk(client):
    import polars as pl
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from datetime import date

    d = date(2026, 8, 26)
    bars = runtime_root() / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    # 独立文件，避免覆盖 fixture 的 000001
    pl.DataFrame({"date": [d], "code": ["000002"], "open": [1.0], "high": [1.0],
                  "low": [1.0], "close": [1.0]}).write_parquet(bars / "000002.parquet")
    store = SyncStore(runtime_root() / "storage")
    store.set_doubtful_days([d])
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "confirmed": ["2026-08-26"]}
    store2 = SyncStore(runtime_root() / "storage")
    assert store2.done_days() == {d}
    assert store2.doubtful_days() == []


def test_submit_execution_dispatch_market_bars_sync(client, monkeypatch):
    """dispatch 到 market_bars_sync；替身 executor——真作业会去拉真实 Tushare
    （网络可用 + 本机已存凭证时等于启动整市场同步，测试随即挂死）。"""
    captured: dict = {}

    def _capture(job_type, run_fn, request):
        captured.update(job_type=job_type, request=request)
        return "job-dispatch-sync"

    monkeypatch.setattr(client.app.state.executor, "submit", _capture)
    resp = client.post(
        "/api/executions",
        json={"type": "market_bars_sync", "params": {"force": False}},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_type"] == "market_bars_sync"
    assert payload["console_url"].startswith("/console/")


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
