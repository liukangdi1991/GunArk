"""update_strategy_settings must return the values actually persisted.

Regression: the response was assembled from the update request
(updates.get("enabled", True) / updates.get("params", {})), so a partial
update returned stale enabled/params that disagreed with the DB row the
resolver actually reads.
"""

import json


def test_update_strategy_settings_returns_persisted_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    from trendradar.app.services.strategy_service import update_strategy_settings

    register_all()
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    with sc.connection() as conn:
        conn.execute(
            "INSERT INTO strategy_settings (strategy_id, enabled, params_json) VALUES (?, ?, ?)",
            ("super_b1", 0, json.dumps({"lookback_n": 20})),
        )
        conn.commit()

    # 只更新 params：enabled 必须保持 DB 里的 disabled，且返回与 DB 一致
    result = update_strategy_settings(sc, "super_b1", {"params": {"lookback_n": 30}})
    assert result["enabled"] is False
    assert result["params"] == {"lookback_n": 30}

    with sc.connection() as conn:
        row = conn.execute(
            "SELECT enabled, params_json FROM strategy_settings WHERE strategy_id = 'super_b1'"
        ).fetchone()
    assert row["enabled"] == 0
    assert json.loads(row["params_json"]) == {"lookback_n": 30}
