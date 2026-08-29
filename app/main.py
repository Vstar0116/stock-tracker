import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.crossover import router as crossover_router
from app.api.health import router as health_router
from app.api.instruments import router as instruments_router
from app.api.portfolio_reports import router as portfolio_reports_router
from app.api.screens import router as screens_router
from app.api.status import router as status_router
from app.api.watchlists import router as watchlists_router
from app.api.zone import router as zone_router
from app.config import settings
from app.db.session import engine
from app.errors import DomainError
from app.logging_config import configure_logging, get_request_id
from app.request_id import RequestIDMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.services import alerting

configure_logging()
logger = logging.getLogger("app")

_is_production = settings.app_env.lower() == "production"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One quick DB ping at boot -- fails loud in container/deploy logs
    # immediately instead of only surfacing on the first /health hit or
    # first real request. entrypoint.sh already runs `alembic upgrade head`
    # before gunicorn starts (see DEPLOYMENT.md), so this is a connectivity
    # check, not a migration-state check -- that's covered separately.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("startup: database reachable")
    except Exception:
        logger.exception("startup: database unreachable")
        raise
    yield


app = FastAPI(
    title="Stock Tracker",
    # Private tool, not a public product (CLAUDE.md) -- no reason to hand an
    # unauthenticated internet visitor the full endpoint/schema map once
    # this is reachable outside localhost. Kept on for local dev convenience.
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

# CORS_ORIGINS env var controls this in production (see DEPLOYMENT.md) --
# defaults to the local Vite dev server only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers only expose a small safelisted set of response headers to
    # cross-origin JS by default -- X-Request-ID (app/request_id.py) isn't
    # in it, so without this the frontend's `res.headers.get('X-Request-ID')`
    # would silently always return null.
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    # Plain `def`, not `async def`: Starlette runs a sync exception handler
    # through the same threadpool it uses for sync routes (see
    # starlette/_exception_handler.py's is_async_callable check) -- keeps
    # this off the event loop, same reasoning as the portfolio-reports
    # upload route fix.
    logger.warning("domain error: %s %s -> %s", request.method, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler for anything that isn't a DomainError or an
    HTTPException (both already have their own handler -- FastAPI's default
    for HTTPException, ours above for DomainError -- and take priority since
    Starlette resolves the most specific type in the exception's MRO first).

    Must preserve the exact "leak nothing" contract
    tests/test_api.py::TestUnhandledErrors already asserts: the response
    body is always this fixed generic string, never str(exc) or a
    traceback -- that used to be Starlette's default handler's job; now
    it's this handler's job to keep doing it explicitly.

    Reuses the alerting webhook (app/services/alerting.py) instead of
    adding a new error-tracking dependency -- it already handles
    credential redaction, delivery-failure isolation, and 6h dedup, and
    until now was wired into the batch jobs only, so an API 500 generated
    zero alerts regardless of frequency.
    """
    # exc_info=exc (not the bare `logger.exception(...)` shortcut, which
    # reads the ambient sys.exc_info()): this handler runs in a threadpool
    # worker thread, not the thread that caught the exception, and
    # sys.exc_info() is thread-local -- passing the exception object
    # directly is what stdlib logging documents for exactly this case, and
    # works because the traceback is an attribute of the exception itself.
    logger.error("unhandled exception: %s %s", request.method, request.url.path, exc_info=exc)
    alerting.send_alert(
        title=f"API error: {type(exc).__name__}",
        detail=f"{request.method} {request.url.path} (request_id={get_request_id()})",
        fingerprint=f"api:{type(exc).__name__}:{request.url.path}",
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(crossover_router)
app.include_router(instruments_router)
app.include_router(watchlists_router)
app.include_router(screens_router)
app.include_router(alerts_router)
app.include_router(status_router)
app.include_router(zone_router)
app.include_router(portfolio_reports_router)
