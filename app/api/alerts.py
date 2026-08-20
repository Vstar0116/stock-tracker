from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, pagination
from app.db.session import get_db
from app.models import Alert, Instrument, Screen, User
from app.schemas.alert import AlertOut
from app.schemas.common import Page

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _to_out(alert: Alert, screen_name: str, symbol: str, exchange: str) -> AlertOut:
    return AlertOut(
        id=alert.id,
        screen_id=alert.screen_id,
        screen_name=screen_name,
        instrument_id=alert.instrument_id,
        symbol=symbol,
        exchange=exchange,
        trade_date=alert.trade_date,
        triggered_at=alert.triggered_at,
        snapshot=alert.snapshot,
        seen=alert.seen,
    )


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    trade_date: date | None = Query(None, alias="date"),
    screen_id: int | None = None,
    seen: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination),
) -> Page[AlertOut]:
    # ownership is a WHERE-clause condition (Screen.user_id), same as the
    # rest of the API -- an alert on someone else's screen is filtered out at
    # the SQL level, not fetched and then checked.
    conditions = [Screen.user_id == current_user.id]
    if trade_date is not None:
        conditions.append(Alert.trade_date == trade_date)
    if screen_id is not None:
        conditions.append(Alert.screen_id == screen_id)
    if seen is not None:
        conditions.append(Alert.seen == seen)

    total = db.execute(
        select(func.count()).select_from(Alert).join(Screen, Screen.id == Alert.screen_id).where(*conditions)
    ).scalar_one()

    rows = db.execute(
        select(Alert, Screen.name, Instrument.symbol, Instrument.exchange)
        .join(Screen, Screen.id == Alert.screen_id)
        .join(Instrument, Instrument.id == Alert.instrument_id)
        .where(*conditions)
        .order_by(Alert.triggered_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()

    items = [_to_out(alert, screen_name, symbol, exchange) for alert, screen_name, symbol, exchange in rows]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{alert_id}/seen", response_model=AlertOut)
def mark_seen(
    alert_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> AlertOut:
    row = db.execute(
        select(Alert, Screen.name, Instrument.symbol, Instrument.exchange)
        .join(Screen, Screen.id == Alert.screen_id)
        .join(Instrument, Instrument.id == Alert.instrument_id)
        .where(Alert.id == alert_id, Screen.user_id == current_user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")

    alert, screen_name, symbol, exchange = row
    alert.seen = True
    db.commit()
    db.refresh(alert)
    return _to_out(alert, screen_name, symbol, exchange)
