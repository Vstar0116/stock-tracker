"""Screen CRUD, plus running one immediately and previewing an unsaved rule
tree. Both preview and run reuse services.screening.compile_screen -- the
same parameterised-query compiler the nightly run_screens job uses -- so a
preview and a saved run can never disagree about what "matches" means.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, get_owned_screen, pagination
from app.db.session import get_db
from app.models import Alert, Screen, User
from app.rate_limit import RateLimiter
from app.schemas.common import Page
from app.schemas.screen import (
    ScreenCreate,
    ScreenFromTextRequest,
    ScreenFromTextResponse,
    ScreenMatchOut,
    ScreenOut,
    ScreenPreviewRequest,
    ScreenUpdate,
    parse_screen_definition,
)
from app.services.nl_screen import NlScreenError, translate_to_rule
from app.services.screening import NON_SNAPSHOT_COLUMNS, compile_screen, latest_trade_date, previous_trade_date

router = APIRouter(prefix="/api/screens", tags=["screens"])

# Unlike preview/run (plain SQL against our own DB -- cheap, unlimited), this
# path calls out to Groq on every request: real external latency and, if the
# free tier ever runs out, real cost. Keyed per-user with a 24h window on the
# same (key, timestamp) table login rate-limiting already uses -- 40/day is
# generous for iterating on a screen's phrasing in one sitting (a handful of
# tries, not dozens) while bounding a 5-user deployment's worst case to ~200
# LLM calls/day.
nl_screen_daily_limiter = RateLimiter(
    key_prefix="nl_screen:user",
    max_requests=40,
    window_seconds=86400,
    message="daily limit for AI-generated screens reached (40/day) -- try again tomorrow, or build the rule manually below",
)


def _snapshot(row) -> dict:
    # Categorical fields (sector/industry/series) can appear here via an
    # "in" rule -- only numeric values get the float cast, or a string like
    # "IT" blows up trying to become a float.
    return {
        k: (float(v) if v is not None and not isinstance(v, str) else v)
        for k, v in row.items()
        if k not in NON_SNAPSHOT_COLUMNS
    }


def _to_matches(rows) -> list[ScreenMatchOut]:
    return [
        ScreenMatchOut(
            instrument_id=row["instrument_id"],
            symbol=row["symbol"],
            exchange=row["exchange"],
            sector=row["sector"],
            trade_date=row["trade_date"],
            close=float(row["close"]) if row["close"] is not None else None,
            day_change_pct=(
                (float(row["close"]) - float(row["prev_close"])) / float(row["prev_close"]) * 100
                if row["close"] is not None and row["prev_close"]
                else None
            ),
            values=_snapshot(row),
        )
        for row in rows
    ]


def _run_compiled(db: Session, stmt) -> list:
    """Run a compile_screen() statement, or return [] if it's None (no prior
    trading day to compare against yet -- not an error, just nothing to show)."""
    if stmt is None:
        return []
    return db.execute(stmt).mappings().all()


def _paginate(matches: list[ScreenMatchOut], page: Pagination) -> Page[ScreenMatchOut]:
    return Page(items=matches[page.offset : page.offset + page.limit], total=len(matches), limit=page.limit, offset=page.offset)


@router.get("", response_model=Page[ScreenOut])
def list_screens(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination),
) -> Page[ScreenOut]:
    total = db.execute(select(func.count()).select_from(Screen).where(Screen.user_id == current_user.id)).scalar_one()
    rows = (
        db.execute(
            select(Screen)
            .where(Screen.user_id == current_user.id)
            .order_by(Screen.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return Page(items=[ScreenOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=ScreenOut, status_code=status.HTTP_201_CREATED)
def create_screen(
    payload: ScreenCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ScreenOut:
    screen = Screen(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        definition=payload.definition.model_dump(mode="json"),
        is_active=payload.is_active,
    )
    db.add(screen)
    db.commit()
    db.refresh(screen)
    return ScreenOut.model_validate(screen)


@router.patch("/{screen_id}", response_model=ScreenOut)
def update_screen(
    payload: ScreenUpdate, screen: Screen = Depends(get_owned_screen), db: Session = Depends(get_db)
) -> ScreenOut:
    if payload.name is not None:
        screen.name = payload.name
    if payload.description is not None:
        screen.description = payload.description
    if payload.definition is not None:
        screen.definition = payload.definition.model_dump(mode="json")
    if payload.is_active is not None:
        screen.is_active = payload.is_active
    db.commit()
    db.refresh(screen)
    return ScreenOut.model_validate(screen)


@router.delete("/{screen_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_screen(screen: Screen = Depends(get_owned_screen), db: Session = Depends(get_db)) -> None:
    db.delete(screen)
    db.commit()


@router.post("/from-text", response_model=ScreenFromTextResponse)
def screen_from_text(
    payload: ScreenFromTextRequest,
    current_user: User = Depends(get_current_user),
) -> ScreenFromTextResponse:
    nl_screen_daily_limiter.check(str(current_user.id))
    try:
        rule = translate_to_rule(payload.text)
    except NlScreenError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ScreenFromTextResponse(definition=rule)


@router.post("/preview", response_model=Page[ScreenMatchOut])
def preview_screen(
    payload: ScreenPreviewRequest,
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
    current_user: User = Depends(get_current_user),  # unused -- enforces auth like every other route here
) -> Page[ScreenMatchOut]:
    as_of_date = latest_trade_date(db)
    if as_of_date is None:
        return _paginate([], page)
    prev_date = previous_trade_date(db, as_of_date)
    rows = _run_compiled(db, compile_screen(payload.definition, as_of_date, prev_date))
    return _paginate(_to_matches(rows), page)


@router.post("/{screen_id}/run", response_model=Page[ScreenMatchOut])
def run_screen_now(
    screen: Screen = Depends(get_owned_screen),
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
) -> Page[ScreenMatchOut]:
    try:
        rule = parse_screen_definition(screen.definition)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"stored screen definition is invalid: {exc}"
        ) from exc

    as_of_date = latest_trade_date(db)
    if as_of_date is None:
        return _paginate([], page)
    prev_date = previous_trade_date(db, as_of_date)
    rows = _run_compiled(db, compile_screen(rule, as_of_date, prev_date))

    if rows:
        values = [
            {
                "screen_id": screen.id,
                "instrument_id": row["instrument_id"],
                "trade_date": row["trade_date"],
                "snapshot": _snapshot(row),
            }
            for row in rows
        ]
        insert_stmt = pg_insert(Alert).values(values)
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=[Alert.screen_id, Alert.instrument_id, Alert.trade_date]
        )
        db.execute(insert_stmt)
        db.commit()

    return _paginate(_to_matches(rows), page)
