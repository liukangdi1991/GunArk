"""Tests: the default strategy group is bootstrapped with all registered strategies."""

from __future__ import annotations

import argparse
import json
import sqlite3

from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _init_storage(tmp_path) -> StorageConnection:
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return sc


def test_ensure_default_group_creates_group_with_all_strategies(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)

    from trendradar.app.services.strategy_service import ensure_default_group

    ensure_default_group(sc)

    conn = sc.connect()
    group = conn.execute(
        "SELECT * FROM strategy_groups WHERE id = 'default'"
    ).fetchone()
    assert group is not None
    assert group["enabled"] == 1

    members = conn.execute(
        "SELECT strategy_id FROM strategy_group_members WHERE group_id = 'default' "
        "ORDER BY sort_order"
    ).fetchall()

    from trendradar.domain.strategy.registry import list_all

    registered = list_all()
    registered_ids = {d.strategy_id for d in registered}
    assert len(members) == len(registered_ids) == len(registered)
    assert {m["strategy_id"] for m in members} == registered_ids


def test_ensure_default_group_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)

    from trendradar.app.services.strategy_service import ensure_default_group

    ensure_default_group(sc)
    ensure_default_group(sc)

    conn = sc.connect()
    from trendradar.domain.strategy.registry import list_all

    expected = len(list_all())
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM strategy_groups WHERE id = 'default'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM strategy_group_members WHERE group_id = 'default'"
        ).fetchone()[0]
        == expected
    )
    assert conn.execute("SELECT COUNT(*) FROM strategy_settings").fetchone()[0] == expected


def test_ensure_default_group_seeds_strategy_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = _init_storage(tmp_path)

    from trendradar.app.services.strategy_service import ensure_default_group

    ensure_default_group(sc)

    conn = sc.connect()
    settings = conn.execute(
        "SELECT strategy_id, enabled, params_json FROM strategy_settings"
    ).fetchall()

    from trendradar.domain.strategy.registry import get, list_all

    assert len(settings) == len(list_all())

    for s in settings:
        assert s["enabled"] == 1
        assert json.loads(s["params_json"]) == get(s["strategy_id"]).default_params


def test_init_v2_creates_default_group(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=False, confirm_reset=False)
    cmd_init_v2(args)

    conn = sqlite3.connect(str(tmp_path / "storage" / "app.db"))
    group = conn.execute(
        "SELECT id FROM strategy_groups WHERE id = 'default'"
    ).fetchone()
    assert group is not None
    from trendradar.domain.strategy.registry import list_all

    members = conn.execute(
        "SELECT COUNT(*) FROM strategy_group_members WHERE group_id = 'default'"
    ).fetchone()[0]
    assert members == len(list_all())
