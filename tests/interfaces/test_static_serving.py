"""Tests: the built frontend is served by the API with SPA fallback."""

from __future__ import annotations


def _build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>TrendRadar Workbench</body></html>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('app');", encoding="utf-8")
    monkeypatch.setenv("TREND_RADAR_FRONTEND_DIST", str(dist))

    from trendradar.interfaces.api.app import create_app

    return create_app()


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    return TestClient(_build_app(tmp_path, monkeypatch))


def test_root_serves_index_html(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "TrendRadar Workbench" in r.text


def test_spa_fallback_for_client_side_routes(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        for route in ["/selections", "/backtests/history", "/console/abc123"]:
            r = client.get(route)
            assert r.status_code == 200
            assert "TrendRadar Workbench" in r.text


def test_static_asset_is_served(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text


def test_api_routes_still_work_beside_static(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/api/strategies")
        assert r.status_code == 200
        assert r.json()


def test_unknown_api_path_returns_json_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404
        assert "application/json" in r.headers["content-type"]


def test_missing_asset_returns_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/assets/missing.js")
        assert r.status_code == 404
