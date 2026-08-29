"""Shared FastAPI dependencies for protected routes."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import PortfolioReport, Screen, User, Watchlist
from app.security import decode_access_token

# auto_error=False so a missing header falls through to our own 401 below,
# instead of HTTPBearer's default 403 -- one consistent status for "not logged in".
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return user


@dataclass
class Pagination:
    limit: int
    offset: int


def pagination(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def get_owned_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Watchlist:
    """Look up a watchlist that belongs to the current user. The ownership
    check is part of the WHERE clause -- a watchlist owned by someone else
    doesn't exist as far as this query is concerned, so it 404s exactly like
    a truly missing id would (never leaks "exists, but isn't yours")."""
    watchlist = db.execute(
        select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == current_user.id)
    ).scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    return watchlist


def get_owned_screen(
    screen_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Screen:
    screen = db.execute(
        select(Screen).where(Screen.id == screen_id, Screen.user_id == current_user.id)
    ).scalar_one_or_none()
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "screen not found")
    return screen


def get_owned_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioReport:
    """Same ownership-in-the-WHERE-clause pattern as get_owned_watchlist/
    get_owned_screen above -- another user's report 404s rather than 403ing,
    so its existence isn't leaked."""
    report = db.execute(
        select(PortfolioReport).where(PortfolioReport.id == report_id, PortfolioReport.user_id == current_user.id)
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return report
