"""个股K线 API 契约测试（spec v2.6 §7）：形状/11键/404/503/422/degraded。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

EXPECTED_BAR_KEYS = {
    "timestamp", "date", "open", "high", "low", "close",
    "pre_close", "volume", "amount", "zx_short", "zx_long",
}  # N21：键集合精确断言是唯一闸门


def _write_kline_bars(storage, code="000001", with_pre_close=True, factors=None):
    factors = factors or (1.2, 1.2, 1.5, 1.5, 1.5)  # 已重建 + 合法除权形态
    bars_dir = storage / "market" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    base = date(2026, 8, 17)  # 周一
    rows = []
    for i, f in enumerate(factors):
        close = 10.0 + i * 0.1
        row = {
            "code": code, "date": base + timedelta(days=i),
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.3,
            "close": close, "volume": 1000.0 + i, "amount": 10000.0 + i,
            "adj_factor": f, "is_suspended": False,
        }
        if with_pre_close:
            row["pre_close"] = close - 0.05
        rows.append(row)
    pl.DataFrame(rows, schema_overrides={"date": pl.Date}).write_parquet(
        bars_dir / f"{code}.parquet"
    )


def _write_meta(storage):
    meta = pl.DataFrame({"code": ["000001"], "name": ["平安银行"], "industry": ["银行"]})
    meta.write_parquet(storage / "market" / "stock_meta.parquet")


@pytest.fixture()
def kline_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir(parents=True)
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    init_schema(StorageConnection(storage).connect())
    _write_kline_bars(storage)
    _write_meta(storage)
    monkeypatch.setenv("TREND_RADAR_FRONTEND_DIST", str(tmp_path / "missing-dist"))

    from fastapi.testclient import TestClient
    from trendradar.interfaces.api.app import create_app

    with TestClient(create_app()) as c:
        yield c, storage


def test_kline_200_shape_and_values(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload.keys()) == {
        "code", "name", "industry", "period", "adjust",
        "adjust_degraded", "last_bar_date", "bars",
    }
    assert payload["code"] == "000001"
    assert payload["name"] == "平安银行" and payload["industry"] == "银行"
    assert payload["period"] == "daily" and payload["adjust"] == "qfq"  # 默认值
    assert payload["adjust_degraded"] is False
    assert payload["last_bar_date"] == "2026-08-21"
    assert len(payload["bars"]) == 5
    assert all(set(b.keys()) == EXPECTED_BAR_KEYS for b in payload["bars"])
    last = payload["bars"][-1]
    assert last["close"] == 10.4  # 末根 scale=1（M1 不变量）
    assert type(last["close"]) is float
    assert last["date"] == "2026-08-21"
    assert last["timestamp"] == int(
        datetime(2026, 8, 21, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000
    )  # M11+N19：Asia/Shanghai 午夜毫秒
    assert payload["bars"][0]["close"] == pytest.approx(10.0 * 1.2 / 1.5)


def test_kline_strict_json_no_nan(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/000001/kline")

    def _reject(const):
        raise AssertionError(f"非 JSON 标准常量: {const}")  # N6：NaN/Inf 不许出现

    json.loads(resp.text, parse_constant=_reject)


def test_kline_degraded_true_on_legacy_const_factor(kline_client, tmp_path):
    client, storage = kline_client
    _write_kline_bars(storage, with_pre_close=False, factors=(1.0,) * 5)  # 存量形态
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["adjust_degraded"] is True  # B1：qfq≡none 属故障态，显式可见
    assert payload["bars"][-1]["close"] == 10.4


def test_kline_weekly_and_monthly(kline_client):
    client, _ = kline_client
    weekly = client.get("/api/stocks/000001/kline?period=weekly").json()
    assert len(weekly["bars"]) == 1  # 2026-08-17(一)~08-21(五) 同周
    assert weekly["bars"][0]["date"] == "2026-08-21"
    monthly = client.get("/api/stocks/000001/kline?period=monthly").json()
    assert len(monthly["bars"]) == 1


def test_kline_404_unknown_code(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/999999/kline")
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)


def test_kline_503_when_bars_dir_missing(kline_client, tmp_path):
    client, storage = kline_client
    import shutil

    shutil.rmtree(storage / "market" / "bars")
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 503  # N7：区别于 404 的可重试语义


def test_kline_503_on_corrupt_file(kline_client, tmp_path):
    client, storage = kline_client
    (storage / "market" / "bars" / "000001.parquet").write_bytes(b"not-parquet")
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 503


def test_kline_422_invalid_inputs(kline_client):
    client, _ = kline_client
    assert client.get("/api/stocks/abc123/kline").status_code == 422  # F3：Path 校验
    assert client.get("/api/stocks/000001/kline?period=yearly").status_code == 422
    assert client.get("/api/stocks/000001/kline?adjust=hfq").status_code == 422


def test_kline_name_fallback_when_meta_missing(kline_client, tmp_path):
    client, storage = kline_client
    (storage / "market" / "stock_meta.parquet").unlink()
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["name"] == "000001" and payload["industry"] is None  # M9


def test_kline_two_row_history_ok(kline_client, tmp_path):
    client, storage = kline_client
    _write_kline_bars(storage, factors=(1.5, 1.5))  # N17：极短历史
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    assert len(resp.json()["bars"]) == 2
