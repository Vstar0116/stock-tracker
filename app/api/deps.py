"""Shared FastAPI dependencies for protected routes."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Holding, Screen, User, Watchlist
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


def _user_watchlist_query(watchlist_id: int, user_id: int):
    """The one place the "does this watchlist belong to this user" query is
    written -- ownership is part of the WHERE clause, so a watchlist owned by
    someone else doesn't exist as far as this query is concerned (never leaks
    "exists, but isn't yours")."""
    return select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user_id)


def get_owned_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Watchlist:
    """Path-param form: watchlist_id is required, missing/foreign id 404s."""
    watchlist = db.execute(_user_watchlist_query(watchlist_id, current_user.id)).scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    return watchlist


def get_optional_owned_watchlist(
    watchlist_id: int | None,
    db: Session,
    current_user: User,
) -> Watchlist | None:
    """Same ownership-scoping as get_owned_watchlist, for endpoints where the
    watchlist id is an optional query param (e.g. "scope this scan to one
    watchlist") rather than a required path param, so it can't be expressed
    as a get_owned_watchlist Depends() directly. None in -> None out (no
    watchlist requested); a given but foreign/missing id still 404s."""
    if watchlist_id is None:
        return None
    watchlist = db.execute(_user_watchlist_query(watchlist_id, current_user.id)).scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    return watchlist


def get_owned_holding(
    holding_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Holding:
    """Same ownership-scoping pattern as get_owned_watchlist -- a holding
    owned by someone else 404s exactly like a missing id would."""
    holding = db.execute(
        select(Holding).where(Holding.id == holding_id, Holding.user_id == current_user.id)
    ).scalar_one_or_none()
    if holding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "holding not found")
    return holding


def owned_screen_clause(user_id: int):
    """The one place "does this screen belong to this user" is written, for
    callers that filter/join over Screen (e.g. alerts) rather than fetching
    a single Screen by id -- see get_owned_screen for that shape."""
    return Screen.user_id == user_id


def get_owned_screen(
    screen_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Screen:
    screen = db.execute(
        select(Screen).where(Screen.id == screen_id, owned_screen_clause(current_user.id))
    ).scalar_one_or_none()
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "screen not found")
    return screen
