from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.core.config import STATIC_DIR
from web.routes import backtests, pages, selections, strategies


app = FastAPI(title="GunArk Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(pages.router)
app.include_router(strategies.router)
app.include_router(selections.router)
app.include_router(backtests.router)
