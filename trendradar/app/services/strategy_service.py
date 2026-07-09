"""Strategy group CRUD and settings management."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from trendradar.domain.strategy.models import StrategySettings
from trendradar.domain.strategy.registry import get, list_all, get_default_params


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _dict_from_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    return d


def list_strategy_groups(store) -> list[dict]:
    """List all strategy groups with members.

    Args:
        store: StorageConnection instance (provides .connect() -> sqlite3.Connection)
    """
    conn = store.connect()
    groups = conn.execute(
        "SELECT id, name, description, enabled, sort_order, created_at, updated_at "
        "FROM strategy_groups ORDER BY sort_order ASC"
    ).fetchall()

    result = []
    for g in groups:
        d = _dict_from_row(g)
        members = conn.execute(
            "SELECT strategy_id, sort_order FROM strategy_group_members "
            "WHERE group_id = ? ORDER BY sort_order ASC",
            (g["id"],),
        ).fetchall()
        d["members"] = [_dict_from_row(m) for m in members]
        result.append(d)
    return result


def create_strategy_group(store, name: str, description: str = "") -> dict:
    conn = store.connect()
    group_id = name.lower().replace(" ", "_")
    now = _now_iso()
    conn.execute(
        "INSERT INTO strategy_groups (id, name, description, enabled, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, 0, ?, ?)",
        (group_id, name, description, now, now),
    )
    conn.commit()
    return {
        "id": group_id,
        "name": name,
        "description": description,
        "enabled": True,
        "sort_order": 0,
        "members": [],
    }


def update_strategy_group(store, group_id: str, updates: dict) -> dict:
    conn = store.connect()

    existing = conn.execute(
        "SELECT * FROM strategy_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"Strategy group '{group_id}' not found")

    now = _now_iso()
    fields = []
    params = []

    if "name" in updates:
        fields.append("name = ?")
        params.append(updates["name"])
    if "description" in updates:
        fields.append("description = ?")
        params.append(updates["description"])
    if "enabled" in updates:
        fields.append("enabled = ?")
        params.append(1 if updates["enabled"] else 0)

    if fields:
        fields.append("updated_at = ?")
        params.append(now)
        params.append(group_id)
        conn.execute(
            f"UPDATE strategy_groups SET {', '.join(fields)} WHERE id = ?",
            params,
        )

    if "members" in updates:
        conn.execute(
            "DELETE FROM strategy_group_members WHERE group_id = ?",
            (group_id,),
        )
        for i, member in enumerate(updates["members"]):
            sid = member if isinstance(member, str) else member.get("strategy_id", "")
            sort_order = i if isinstance(member, str) else member.get("sort_order", i)
            conn.execute(
                "INSERT INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES (?, ?, ?)",
                (group_id, sid, sort_order),
            )

    conn.commit()

    return _get_group_dict(conn, group_id)


def delete_strategy_group(store, group_id: str) -> dict:
    if group_id == "default":
        raise ValueError("Cannot delete the default strategy group")
    conn = store.connect()
    existing = conn.execute(
        "SELECT * FROM strategy_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"Strategy group '{group_id}' not found")

    result = _dict_from_row(existing)
    _migrate_orphan_strategies(conn, group_id)
    conn.execute("DELETE FROM strategy_group_members WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM strategy_groups WHERE id = ?", (group_id,))
    conn.commit()
    return result


def _migrate_orphan_strategies(conn, group_id: str) -> None:
    """Move strategies that only belong to the deleted group back to default."""
    orphan = conn.execute(
        "SELECT strategy_id FROM strategy_group_members WHERE group_id = ? "
        "AND strategy_id NOT IN (SELECT strategy_id FROM strategy_group_members WHERE group_id != ?)",
        (group_id, group_id),
    ).fetchall()
    max_sort = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM strategy_group_members WHERE group_id = 'default'"
    ).fetchone()[0]
    for i, row in enumerate(orphan):
        conn.execute(
            "INSERT OR IGNORE INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES (?, ?, ?)",
            ("default", row["strategy_id"], max_sort + 1 + i),
        )


def list_strategies() -> list[dict]:
    """List all registered strategies from the registry."""
    defs = list_all()
    return [
        {
            "strategy_id": d.strategy_id,
            "name": d.name,
            "description": d.description,
            "default_params": dict(d.default_params),
        }
        for d in defs
    ]


def get_strategy_settings(store) -> list[dict]:
    """Get all strategy settings from the database."""
    conn = store.connect()
    rows = conn.execute(
        "SELECT strategy_id, enabled, params_json, updated_at FROM strategy_settings"
    ).fetchall()

    defs = list_all()
    def_by_id = {d.strategy_id: d for d in defs}

    result = []
    seen = set()
    for row in rows:
        d = _dict_from_row(row)
        sid = d["strategy_id"]
        seen.add(sid)
        result.append({
            "strategy_id": sid,
            "name": def_by_id[sid].name if sid in def_by_id else sid,
            "enabled": bool(d.get("enabled", True)),
            "params": json.loads(d.get("params_json", "{}")),
            "default_params": get_default_params(sid),
            "updated_at": d.get("updated_at"),
        })

    for d in defs:
        if d.strategy_id not in seen:
            result.append({
                "strategy_id": d.strategy_id,
                "name": d.name,
                "enabled": True,
                "params": dict(d.default_params),
                "default_params": dict(d.default_params),
                "updated_at": None,
            })

    return result


def update_strategy_settings(store, strategy_id: str, updates: dict) -> dict:
    conn = store.connect()
    now = _now_iso()

    defn = get(strategy_id)
    if defn is None:
        raise ValueError(f"Strategy '{strategy_id}' not found in registry")

    existing = conn.execute(
        "SELECT * FROM strategy_settings WHERE strategy_id = ?", (strategy_id,)
    ).fetchone()

    enabled = updates.get("enabled", True)
    params = updates.get("params", {})
    if existing is None:
        if not isinstance(enabled, bool):
            enabled = True
        params_json = json.dumps(params, ensure_ascii=False)
        conn.execute(
            "INSERT INTO strategy_settings (strategy_id, enabled, params_json, updated_at) VALUES (?, ?, ?, ?)",
            (strategy_id, 1 if enabled else 0, params_json, now),
        )
    else:
        if "enabled" in updates:
            enabled_val = 1 if updates["enabled"] else 0
        else:
            enabled_val = existing["enabled"]
        if "params" in updates:
            params_json = json.dumps(params, ensure_ascii=False)
        else:
            params_json = existing["params_json"]
        conn.execute(
            "UPDATE strategy_settings SET enabled = ?, params_json = ?, updated_at = ? WHERE strategy_id = ?",
            (enabled_val, params_json, now, strategy_id),
        )

    conn.commit()

    return {
        "strategy_id": strategy_id,
        "name": defn.name,
        "enabled": enabled,
        "params": params,
        "default_params": dict(defn.default_params),
        "updated_at": now,
    }


def _get_group_dict(conn: sqlite3.Connection, group_id: str) -> dict:
    group = conn.execute(
        "SELECT * FROM strategy_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if group is None:
        raise ValueError(f"Strategy group '{group_id}' not found")
    d = _dict_from_row(group)
    members = conn.execute(
        "SELECT strategy_id, sort_order FROM strategy_group_members "
        "WHERE group_id = ? ORDER BY sort_order ASC",
        (group_id,),
    ).fetchall()
    d["members"] = [_dict_from_row(m) for m in members]
    return d
