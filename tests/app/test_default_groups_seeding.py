"""测试：默认策略组按「指标域」播种五个组（替换旧版平铺 default 组引导）。

语义：
- 空表 → 直接播种五个指标域组
- 仅有旧版平铺 default 组（系统引导痕迹，无分组信息）→ 迁移为五组
- 存在任何其他组（用户已定制）→ 分组数据一律不碰
- strategy_settings 兜底行始终为全部注册策略补齐
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


EXPECTED_GROUPS = [
    ("mt_brick", "MT 砖型图",
     ["brick_chart", "ultimate_brick_chart", "oversold_bottom_fishing", "oversold_rebound"]),
    ("zx_line", "多空线",
     ["super_b1", "zxdkx_balance", "volume_spike_balance", "duo_kong_trend_balance"]),
    ("kdj", "KDJ 超卖",
     ["bbi_kdj_b1", "perfect_b1_v2", "peak_kdj", "single_needle_down_20"]),
    ("ma_macd", "均线·MACD",
     ["bbi_short_long", "macd_ma_convergence", "ma60_volume_wave"]),
    ("volume_price", "量价形态",
     ["big_bullish_volume"]),
]


def _init_storage(tmp_path) -> StorageConnection:
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return sc


def _groups(conn: sqlite3.Connection) -> list[tuple]:
    rows = conn.execute(
        "SELECT id, name, sort_order FROM strategy_groups ORDER BY sort_order, id"
    ).fetchall()
    return [(r["id"], r["name"], r["sort_order"]) for r in rows]


def _members(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT group_id, strategy_id FROM strategy_group_members ORDER BY group_id, sort_order"
    ).fetchall():
        out.setdefault(r["group_id"], []).append(r["strategy_id"])
    return out


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()
    return _init_storage(tmp_path)


def test_seeds_five_indicator_groups_on_empty_store(store):
    from trendradar.app.services.strategy_service import ensure_default_groups

    ensure_default_groups(store)

    conn = store.connect()
    assert _groups(conn) == [
        (gid, gname, i) for i, (gid, gname, _) in enumerate(EXPECTED_GROUPS)
    ]
    members = _members(conn)
    assert members == {gid: ids for gid, _, ids in EXPECTED_GROUPS}


def test_migrates_legacy_flat_default_group(store):
    """旧版引导留下的平铺 default 组（哪怕缺新策略）应被替换为五组。"""
    from trendradar.app.services.strategy_service import ensure_default_groups

    conn = store.connect()
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO strategy_groups (id, name, description, enabled, sort_order, created_at, updated_at) "
        "VALUES ('default', '默认组', '系统默认策略组', 1, 0, ?, ?)",
        (now, now),
    )
    for i, d in enumerate(
        __import__("trendradar.domain.strategy.registry", fromlist=["list_all"]).list_all()[:3]
    ):
        conn.execute(
            "INSERT INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES ('default', ?, ?)",
            (d.strategy_id, i),
        )
    conn.commit()
    conn.close()

    ensure_default_groups(store)

    conn = store.connect()
    ids = [g[0] for g in _groups(conn)]
    assert "default" not in ids
    assert ids == [gid for gid, _, _ in EXPECTED_GROUPS]
    assert _members(conn) == {gid: ids2 for gid, _, ids2 in EXPECTED_GROUPS}


def test_leaves_customized_groups_untouched(store):
    from trendradar.app.services.strategy_service import ensure_default_groups

    conn = store.connect()
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO strategy_groups (id, name, description, enabled, sort_order, created_at, updated_at) "
        "VALUES ('mine', '我的组', '', 1, 0, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES ('mine', 'brick_chart', 0)"
    )
    conn.commit()
    conn.close()

    ensure_default_groups(store)

    conn = store.connect()
    assert _groups(conn) == [("mine", "我的组", 0)]
    assert _members(conn) == {"mine": ["brick_chart"]}


def test_is_idempotent(store):
    from trendradar.app.services.strategy_service import ensure_default_groups

    ensure_default_groups(store)
    conn = store.connect()
    groups_first, members_first = _groups(conn), _members(conn)
    conn.close()

    ensure_default_groups(store)

    conn = store.connect()
    assert _groups(conn) == groups_first
    assert _members(conn) == members_first


def test_settings_bootstrapped_for_all_registered(store):
    from trendradar.app.services.strategy_service import ensure_default_groups
    from trendradar.domain.strategy.registry import list_all

    ensure_default_groups(store)

    conn = store.connect()
    rows = conn.execute("SELECT strategy_id, params_json FROM strategy_settings").fetchall()
    settings = {r["strategy_id"]: json.loads(r["params_json"]) for r in rows}
    assert set(settings) == {d.strategy_id for d in list_all()}
    for d in list_all():
        assert settings[d.strategy_id] == dict(d.default_params)
