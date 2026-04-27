from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from web.core.config import STATIC_DIR


router = APIRouter(tags=["pages"])


@router.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
