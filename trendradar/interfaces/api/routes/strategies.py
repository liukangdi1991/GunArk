"""Strategy group and settings routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from trendradar.interfaces.api.schemas.strategy import (
    StrategyGroupCreate,
    StrategyGroupResponse,
    StrategyGroupUpdate,
    StrategyListItem,
    StrategySettingsResponse,
    StrategySettingsUpdate,
    MemberAdd,
    MemberUpdate,
    ReorderRequest,
)

router = APIRouter(prefix="/api", tags=["strategies"])


def _store(request: FastAPIRequest):
    return request.app.state.store


@router.get("/strategy-groups", response_model=list[StrategyGroupResponse])
def list_strategy_groups(request: FastAPIRequest):
    from trendradar.app.services.strategy_service import list_strategy_groups
    return list_strategy_groups(_store(request))


@router.post("/strategy-groups", response_model=StrategyGroupResponse, status_code=201)
def create_strategy_group(body: StrategyGroupCreate, request: FastAPIRequest):
    from trendradar.app.services.strategy_service import create_strategy_group
    try:
        return create_strategy_group(_store(request), body.name, body.description)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/strategy-groups/{group_id}", response_model=StrategyGroupResponse)
def update_strategy_group(group_id: str, body: StrategyGroupUpdate, request: FastAPIRequest):
    from trendradar.app.services.strategy_service import update_strategy_group
    updates = body.model_dump(exclude_none=True)
    try:
        return update_strategy_group(_store(request), group_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/strategy-groups/{group_id}")
def delete_strategy_group(group_id: str, request: FastAPIRequest):
    from trendradar.app.services.strategy_service import delete_strategy_group
    try:
        result = delete_strategy_group(_store(request), group_id)
        return {"data": dict(result)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/strategy-groups/{group_id}/members", response_model=StrategyGroupResponse)
def add_member(group_id: str, body: MemberAdd, request: FastAPIRequest):
    from trendradar.app.services.strategy_service import update_strategy_group

    store = _store(request)
    conn = store.connect()
    group = conn.execute("SELECT * FROM strategy_groups WHERE id = ?", (group_id,)).fetchone()
    if group is None:
        raise HTTPException(status_code=404, detail=f"Strategy group '{group_id}' not found")

    members = conn.execute(
        "SELECT strategy_id, sort_order FROM strategy_group_members WHERE group_id = ? ORDER BY sort_order ASC",
        (group_id,),
    ).fetchall()
    existing = [dict(m) for m in members]
    max_sort = max((m["sort_order"] for m in existing), default=-1)

    conn.execute(
        "INSERT OR IGNORE INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES (?, ?, ?)",
        (group_id, body.strategy_id, max_sort + 1),
    )
    conn.commit()

    members_after = conn.execute(
        "SELECT strategy_id, sort_order FROM strategy_group_members WHERE group_id = ? ORDER BY sort_order ASC",
        (group_id,),
    ).fetchall()
    result = dict(group)
    result["members"] = [dict(m) for m in members_after]
    return result


@router.delete("/strategy-groups/{group_id}/members/{strategy_id}")
def remove_member(group_id: str, strategy_id: str, request: FastAPIRequest):
    store = _store(request)
    conn = store.connect()
    conn.execute(
        "DELETE FROM strategy_group_members WHERE group_id = ? AND strategy_id = ?",
        (group_id, strategy_id),
    )
    conn.commit()
    return {"data": {"group_id": group_id, "strategy_id": strategy_id, "removed": True}}


@router.patch("/strategy-groups/{group_id}/members/{strategy_id}", response_model=StrategyGroupResponse)
def update_member(group_id: str, strategy_id: str, body: MemberUpdate, request: FastAPIRequest):
    store = _store(request)
    conn = store.connect()

    group = conn.execute("SELECT * FROM strategy_groups WHERE id = ?", (group_id,)).fetchone()
    if group is None:
        raise HTTPException(status_code=404, detail=f"Strategy group '{group_id}' not found")

    member = conn.execute(
        "SELECT * FROM strategy_group_members WHERE group_id = ? AND strategy_id = ?",
        (group_id, strategy_id),
    ).fetchone()
    if member is None:
        raise HTTPException(status_code=404, detail=f"Member '{strategy_id}' not found in group '{group_id}'")

    if body.sort_order is not None:
        conn.execute(
            "UPDATE strategy_group_members SET sort_order = ? WHERE group_id = ? AND strategy_id = ?",
            (body.sort_order, group_id, strategy_id),
        )
        conn.commit()

    members = conn.execute(
        "SELECT strategy_id, sort_order FROM strategy_group_members WHERE group_id = ? ORDER BY sort_order ASC",
        (group_id,),
    ).fetchall()
    result = dict(group)
    result["members"] = [dict(m) for m in members]
    return result


@router.post("/strategy-groups/{group_id}/members/reorder", response_model=StrategyGroupResponse)
def reorder_members(group_id: str, body: ReorderRequest, request: FastAPIRequest):
    store = _store(request)
    conn = store.connect()

    group = conn.execute("SELECT * FROM strategy_groups WHERE id = ?", (group_id,)).fetchone()
    if group is None:
        raise HTTPException(status_code=404, detail=f"Strategy group '{group_id}' not found")

    try:
        conn.execute("DELETE FROM strategy_group_members WHERE group_id = ?", (group_id,))
        for i, sid in enumerate(body.members):
            conn.execute(
                "INSERT INTO strategy_group_members (group_id, strategy_id, sort_order) VALUES (?, ?, ?)",
                (group_id, sid, i),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to reorder members")

    members = conn.execute(
        "SELECT strategy_id, sort_order FROM strategy_group_members WHERE group_id = ? ORDER BY sort_order ASC",
        (group_id,),
    ).fetchall()
    result = dict(group)
    result["members"] = [dict(m) for m in members]
    return result


@router.get("/strategies", response_model=list[StrategyListItem])
def list_strategies():
    from trendradar.app.services.strategy_service import list_strategies
    return list_strategies()


@router.patch("/strategies/{strategy_id}/settings", response_model=StrategySettingsResponse)
def update_strategy_settings(strategy_id: str, body: StrategySettingsUpdate, request: FastAPIRequest):
    from trendradar.app.services.strategy_service import update_strategy_settings
    updates = body.model_dump(exclude_none=True)
    try:
        return update_strategy_settings(_store(request), strategy_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
