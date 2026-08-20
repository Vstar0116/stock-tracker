from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.instruments import router as instruments_router
from app.api.screens import router as screens_router
from app.api.status import router as status_router
from app.api.watchlists import router as watchlists_router

app = FastAPI(title="Stock Tracker")

# Private tool, 4-5 known users, no public deployment planned -- a fixed
# localhost dev-server origin is enough. Widen this if/when the frontend
# gets a real deployment URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(instruments_router)
app.include_router(watchlists_router)
app.include_router(screens_router)
app.include_router(alerts_router)
app.include_router(status_router)
