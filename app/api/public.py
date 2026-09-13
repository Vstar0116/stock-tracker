"""The only router with no get_current_user dependency -- backs the
marketing landing page shown before sign-in. See
app/services/market_snapshot.py for why every field here is safe to expose
without auth (whole-market data NSE already publishes, plus bare counts)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.rate_limit import RateLimiter
from app.schemas.public import MarketSnapshotOut
from app.services.market_snapshot import build_snapshot

router = APIRouter(prefix="/api/public", tags=["public"])

# IP-keyed since there's no logged-in user to key on. Defense in depth only:
# the actual aggregation is cached per trade_date (build_snapshot), so a
# repeat hit in the same trading day is a cache read, not fresh work.
snapshot_limiter = RateLimiter(
    key_prefix="public:market-snapshot",
    max_requests=60,
    window_seconds=60,
    message="too many requests, try again shortly",
)


@router.get("/market-snapshot", response_model=MarketSnapshotOut, dependencies=[Depends(snapshot_limiter)])
def market_snapshot(db: Session = Depends(get_db)) -> MarketSnapshotOut:
    try:
        return build_snapshot(db)
    except ValueError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
