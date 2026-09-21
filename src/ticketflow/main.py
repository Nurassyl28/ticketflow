from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from ticketflow.config import Settings
from ticketflow.database import build_engine
from ticketflow.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(settings)
        app.state.engine = engine
        try:
            yield
        finally:
            await run_in_threadpool(engine.dispose)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app
