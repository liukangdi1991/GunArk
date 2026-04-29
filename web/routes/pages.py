from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from web.core.config import FRONTEND_DIST_DIR


router = APIRouter(tags=["pages"])


def _react_index() -> FileResponse:
    index = FRONTEND_DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=503, detail="前端尚未构建，请先执行 npm run build")


@router.get("/")
def index() -> FileResponse:
    return _react_index()


@router.get("/app")
def react_app() -> FileResponse:
    return _react_index()


@router.get("/console/{execution_id}")
def console(execution_id: str) -> FileResponse:
    return _react_index()


@router.get("/selections")
def selections() -> FileResponse:
    return _react_index()


@router.get("/selections/{execution_key}")
def selection_result(execution_key: str) -> FileResponse:
    return _react_index()


@router.get("/backtests")
def backtests() -> FileResponse:
    return _react_index()


@router.get("/backtests/history")
def backtest_from_selection() -> FileResponse:
    return _react_index()


@router.get("/backtests/selection-backtest")
def selection_backtest() -> FileResponse:
    return _react_index()


@router.get("/backtests/{execution_key}")
def backtest_report(execution_key: str) -> FileResponse:
    return _react_index()


@router.get("/market-data")
def market_data() -> FileResponse:
    return _react_index()
