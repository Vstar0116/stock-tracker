from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from app.security_headers import SecurityHeadersMiddleware

_is_production = settings.app_env.lower() == "production"

app = FastAPI(
    title="Stock Tracker",
    # Private tool, not a public product (CLAUDE.md) -- no reason to hand an
    # unauthenticated internet visitor the full endpoint/schema map once
    # this is reachable outside localhost. Kept on for local dev convenience.
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)

# CORS_ORIGINS env var controls this in production (see DEPLOYMENT.md) --
# defaults to the local Vite dev server only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
