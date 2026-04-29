from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.core.config import FRONTEND_DIST_DIR
from web.routes import (
    backtest_results,
    executions,
    market_data,
    pages,
    selection_results,
    strategies,
)


app = FastAPI(title="日线观势 API")


if (FRONTEND_DIST_DIR / "assets").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST_DIR / "assets"),
        name="frontend-assets",
    )
app.include_router(pages.router)
app.include_router(strategies.router)
app.include_router(market_data.router)
app.include_router(selection_results.router)
app.include_router(backtest_results.router)
app.include_router(executions.router)
