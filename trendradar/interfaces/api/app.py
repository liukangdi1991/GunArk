"""FastAPI application assembly for TrendRadar V2."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.artifact_store import ArtifactStore
    from trendradar.infrastructure.storage.schema import init_schema
    from trendradar.domain.market.data_store import LocalParquetMarketStore
    from trendradar.domain.signal.repository import SignalRepository
    from trendradar.domain.strategy.selectors import register_all
    from trendradar.app.jobs.persistence import JobStore
    from trendradar.app.jobs.executor import JobExecutor

    register_all()

    storage_root = runtime_root() / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    store = StorageConnection(storage_root)
    init_schema(store.connect())

    artifact_store = ArtifactStore(storage_root)
    signal_repo = SignalRepository(artifact_store)

    market_store = LocalParquetMarketStore(storage_root / "market" / "bars")

    job_store = JobStore(store.db_path)
    executor = JobExecutor(job_store)

    app.state.store = store
    app.state.artifact_store = artifact_store
    app.state.signal_repo = signal_repo
    app.state.market_store = market_store
    app.state.executor = executor

    yield

    executor.shutdown(wait=True)


def create_app() -> FastAPI:
    app = FastAPI(
        title="TrendRadar V2 API",
        version="2.0.0",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from trendradar.interfaces.api.routes.strategies import router as strategies_router
    from trendradar.interfaces.api.routes.executions import router as executions_router
    from trendradar.interfaces.api.routes.market import router as market_router
    from trendradar.interfaces.api.routes.backtest import router as backtest_router

    app.include_router(strategies_router)
    app.include_router(executions_router)
    app.include_router(market_router)
    app.include_router(backtest_router)

    return app


app = create_app()
