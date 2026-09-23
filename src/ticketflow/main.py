from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ticketflow.access import orders_router, organizer_router
from ticketflow.analytics import router as analytics_router
from ticketflow.auth.router import admin_router
from ticketflow.auth.router import router as auth_router
from ticketflow.cancellations import router as cancellations_router
from ticketflow.catalog import router as catalog_router
from ticketflow.checkout import router as checkout_router
from ticketflow.config import Settings
from ticketflow.database import build_engine
from ticketflow.health import router as health_router
from ticketflow.reservations import router as reservations_router
from ticketflow.tickets import router as tickets_router


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
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Never echo submitted passwords/tokens in validation responses.
        details = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
        return JSONResponse(status_code=422, content={"detail": details})

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(orders_router)
    app.include_router(organizer_router)
    app.include_router(catalog_router)
    app.include_router(reservations_router)
    app.include_router(checkout_router)
    app.include_router(tickets_router)
    app.include_router(cancellations_router)
    app.include_router(analytics_router)
    return app
