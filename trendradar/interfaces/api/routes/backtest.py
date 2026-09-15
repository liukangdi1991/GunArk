"""Backtest results routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request as FastAPIRequest, Response
from fastapi.responses import JSONResponse

from trendradar.interfaces.api.schemas.execution import (
    BacktestRequest as BacktestSubmitRequest,
    DeleteKeysRequest,
    SelectionBacktestRequest,
)

router = APIRouter(prefix="/api", tags=["backtest"])

# 报告响应形状版本：改结构时必须 +1。ETag 只绑产物身份（mtime+size）时，产物没变 → 304 →
# 浏览器继续复用旧形状的缓存体（实测踩过：P2 瘦身后页面拿到旧 payload 直接崩）。
BACKTEST_REPORT_ETAG_VERSION = 2


def _executor(request: FastAPIRequest):
    return request.app.state.executor


def _market_store(request: FastAPIRequest):
    return request.app.state.market_store


def _signal_repo(request: FastAPIRequest):
    return request.app.state.signal_repo


def _store(request: FastAPIRequest):
    return request.app.state.store


def _artifacts_root(request: FastAPIRequest) -> Path:
    from trendradar.infrastructure.runtime import runtime_root
    return runtime_root() / "storage" / "objects" / "executions"


@router.get("/backtest-results")
def list_backtest_results(
    request: FastAPIRequest,
    limit: int | None = Query(default=None, ge=1),
):
    from trendradar.interfaces.api.presenters import list_backtest_results_payload
    return list_backtest_results_payload(limit=limit)


@router.get("/backtest-results/{execution_key}/report")
def get_backtest_report(execution_key: str, request: FastAPIRequest):
    from trendradar.interfaces.api.presenters import BacktestSourceUnreadable, backtest_result_payload

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    root = _artifacts_root(request)
    result_file = root / execution_key / "backtest" / "result.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")

    # 产物级不变数据：ETag 直接取文件身份（mtime_ns + size），无需读盘算哈希。
    # 条件请求命中即 304——报告重达 15MB，重复打开不该重算也不该重传。
    stat = result_file.stat()
    etag = f'"{BACKTEST_REPORT_ETAG_VERSION}-{stat.st_mtime_ns}-{stat.st_size}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    try:
        payload = backtest_result_payload(execution_key, include_report=True)
    except BacktestSourceUnreadable as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse(payload, headers=headers)


@router.get("/backtest-results/{execution_key}/trades")
def get_backtest_trades(
    execution_key: str,
    request: FastAPIRequest,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    strategies: list[str] | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    """成交明细分页（P2）：只回当前页，筛选/排序在服务端完成。"""
    return _paged_dataset(
        execution_key, "trades",
        offset=offset, limit=limit, strategies=strategies, sort_by=sort_by, sort_dir=sort_dir,
    )


@router.get("/backtest-results/{execution_key}/skips")
def get_backtest_skips(
    execution_key: str,
    request: FastAPIRequest,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    strategies: list[str] | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    """跳过记录分页（P2）。"""
    return _paged_dataset(
        execution_key, "skips",
        offset=offset, limit=limit, strategies=strategies, sort_by=sort_by, sort_dir=sort_dir,
    )


@router.get("/backtest-results/{execution_key}/open-positions")
def get_backtest_open_positions(
    execution_key: str,
    request: FastAPIRequest,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    strategies: list[str] | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    """期末未平仓分页（P2）。"""
    return _paged_dataset(
        execution_key, "open-positions",
        offset=offset, limit=limit, strategies=strategies, sort_by=sort_by, sort_dir=sort_dir,
    )


@router.get("/backtest-results/{execution_key}/marks")
def get_backtest_marks(
    execution_key: str,
    request: FastAPIRequest,
    code: str = Query(pattern=r"^\d{6}$"),
):
    """个股日K上的回测买卖标记（B/S）：只回该股该次回测的成交，不分页。"""
    from trendradar.interfaces.api.presenters import BacktestSourceUnreadable, backtest_marks

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    try:
        payload = backtest_marks(execution_key, code)
    except BacktestSourceUnreadable as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"Backtest result not found for '{execution_key}'"
        )
    return payload


def _paged_dataset(
    execution_key: str,
    dataset: str,
    *,
    offset: int,
    limit: int,
    strategies: list[str] | None,
    sort_by: str | None,
    sort_dir: str,
) -> dict:
    """三个明细端点共用的取数与校验（白名单按数据集取）。"""
    from trendradar.interfaces.api.presenters import (
        BACKTEST_SORT_KEYS,
        BacktestSourceUnreadable,
        backtest_rows_page,
    )

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    if sort_by is not None and sort_by not in BACKTEST_SORT_KEYS[dataset]:
        raise HTTPException(status_code=400, detail=f"非法 sort_by: {sort_by}")

    try:
        payload = backtest_rows_page(
            execution_key, dataset,
            offset=offset, limit=limit, strategies=strategies, sort_by=sort_by, sort_dir=sort_dir,
        )
    except BacktestSourceUnreadable as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"Backtest result not found for '{execution_key}'"
        )
    return payload


def _etag_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match：`*` 匹配一切；逗号列表逐项比；弱校验前缀 W/ 忽略。"""
    if not header:
        return False
    if header.strip() == "*":
        return True
    for candidate in header.split(","):
        token = candidate.strip()
        if token.startswith("W/"):
            token = token[2:]
        if token == etag:
            return True
    return False


@router.delete("/backtest-results/{execution_key}")
def delete_backtest_result(execution_key: str, request: FastAPIRequest):
    # "." must not resolve to the executions root itself, and only directories
    # confirmed to hold a backtest may be deleted (see bulk_delete_backtests).
    if "/" in execution_key or "\\" in execution_key or ".." in execution_key or "." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    from trendradar.interfaces.api.presenters import bulk_delete_backtests

    result = bulk_delete_backtests([execution_key])
    if result["deleted"] == 0:
        raise HTTPException(status_code=404, detail=f"Backtest result not found for '{execution_key}'")
    return result


@router.delete("/backtest-results")
def delete_backtest_results(
    body: DeleteKeysRequest | None = None,
    request: FastAPIRequest = None,
):
    """Delete only the requested backtests; a body-less request clears all."""
    from trendradar.interfaces.api.presenters import bulk_delete_backtests

    keys = body.execution_keys if body else None
    return bulk_delete_backtests(keys)


@router.post("/backtests")
def submit_backtest_route(body: BacktestSubmitRequest, request: FastAPIRequest):
    from datetime import date
    from trendradar.app.services.backtest_service import (
        submit_backtest,
        validate_backtest_prerequisites,
    )
    from trendradar.interfaces.api.presenters import (
        _now_iso,
        trading_dates_payload,
    )

    req = body.model_dump(exclude_none=True)
    execution_key = req.get("execution_key")
    signal_set = _signal_repo(request).load(execution_key) if execution_key else None
    if signal_set is None:
        raise HTTPException(status_code=404, detail=f"信号集不存在: {execution_key}")

    # Prerequisites: signal dates must leave enough trading days for buy + hold.
    # 超短线（entry_on_signal_day）只需持有期天数，T+1 需要多 1 天。
    dates = [date.fromisoformat(d) for d in trading_dates_payload()["dates"]]
    execution = req.get("execution") or {}
    reasons = validate_backtest_prerequisites(
        signal_set,
        dates,
        fixed_hold_n_days=execution.get("fixed_hold_n_days", 5),
        entry_on_signal_day=execution.get("entry_on_signal_day", False),
    )
    if reasons:
        raise HTTPException(status_code=400, detail="；".join(reasons))

    try:
        job_id = submit_backtest(
            _executor(request),
            _market_store(request),
            _signal_repo(request),
            req,
        )
        return {
            "execution_id": job_id,
            "execution_type": "backtest",
            "type": "backtest",
            "status": "submitted",
            "progress_current": 0,
            "progress_total": 0,
            "progress_message": "",
            "error_message": None,
            "result_url": None,
            "console_url": f"/console/{job_id}",
            "created_at": _now_iso(),
        }
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="提交失败，请查看执行控制台日志")


@router.post("/selection-backtest")
def submit_selection_backtest_route(body: SelectionBacktestRequest, request: FastAPIRequest):
    from trendradar.app.services.backtest_service import submit_selection_backtest
    from trendradar.app.services.selection_service import validate_selection_request

    req = body.model_dump(exclude_none=True)
    # 与 /api/executions 保持同一口径：策略既可用 id 也可用中文名（前端下拉传名）
    from trendradar.domain.strategy.registry import to_strategy_ids

    if req.get("strategies"):
        req["strategies"] = to_strategy_ids(req["strategies"])
    try:
        validate_selection_request(req, _store(request), _market_store(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        job_id = submit_selection_backtest(
            _executor(request),
            _market_store(request),
            _signal_repo(request),
            req,
        )
        return {"data": {"job_id": job_id, "status": "submitted"}}
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_selection_backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="提交失败，请查看执行控制台日志")
