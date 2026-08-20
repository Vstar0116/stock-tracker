from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, pagination
from app.db.session import get_db
from app.models import DailyPrice, Indicator, Instrument
from app.schemas.common import Page
from app.schemas.instrument import IndicatorOut, InstrumentDetail, InstrumentOut, PriceOut

router = APIRouter(prefix="/api/instruments", tags=["instruments"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=Page[InstrumentOut])
def list_instruments(
    q: str | None = Query(None, description="matches symbol or company name"),
    sector: str | None = None,
    exchange: str | None = None,
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
) -> Page[InstrumentOut]:
    stmt = select(Instrument)
    count_stmt = select(func.count()).select_from(Instrument)
    if q:
        cond = or_(Instrument.symbol.ilike(f"%{q}%"), Instrument.company_name.ilike(f"%{q}%"))
        stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)
    if sector:
        stmt, count_stmt = stmt.where(Instrument.sector == sector), count_stmt.where(Instrument.sector == sector)
    if exchange:
        stmt, count_stmt = stmt.where(Instrument.exchange == exchange), count_stmt.where(Instrument.exchange == exchange)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(stmt.order_by(Instrument.symbol).limit(page.limit).offset(page.offset)).scalars().all()
    return Page(items=[InstrumentOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.get("/{instrument_id}", response_model=InstrumentDetail)
def get_instrument(instrument_id: int, db: Session = Depends(get_db)) -> InstrumentDetail:
    instrument = db.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    latest_indicator = db.execute(
        select(Indicator)
        .where(Indicator.instrument_id == instrument_id)
        .order_by(Indicator.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    # Last two trade dates, not just one -- day change needs yesterday's
    # close, and this is cheap off the same (instrument_id, trade_date DESC)
    # index the rest of the app already relies on.
    last_two_prices = db.execute(
        select(DailyPrice.trade_date, DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == instrument_id)
        .order_by(DailyPrice.trade_date.desc())
        .limit(2)
    ).all()
    latest_trade_date = last_two_prices[0].trade_date if last_two_prices else None
    latest_close = float(last_two_prices[0].adjusted_close) if last_two_prices else None
    day_change_abs = day_change_pct = None
    if len(last_two_prices) == 2:
        prev_close = float(last_two_prices[1].adjusted_close)
        day_change_abs = latest_close - prev_close
        day_change_pct = (day_change_abs / prev_close) * 100 if prev_close else None

    return InstrumentDetail(
        id=instrument.id,
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        company_name=instrument.company_name,
        series=instrument.series,
        sector=instrument.sector,
        industry=instrument.industry,
        is_active=instrument.is_active,
        isin=instrument.isin,
        listed_date=instrument.listed_date,
        latest_indicators=IndicatorOut.model_validate(latest_indicator) if latest_indicator else None,
        latest_trade_date=latest_trade_date,
        latest_close=latest_close,
        day_change_abs=day_change_abs,
        day_change_pct=day_change_pct,
    )


@router.get("/{instrument_id}/prices", response_model=Page[PriceOut])
def get_instrument_prices(
    instrument_id: int,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
) -> Page[PriceOut]:
    if db.get(Instrument, instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    stmt = select(DailyPrice).where(DailyPrice.instrument_id == instrument_id)
    count_stmt = select(func.count()).select_from(DailyPrice).where(DailyPrice.instrument_id == instrument_id)
    if from_date:
        stmt, count_stmt = stmt.where(DailyPrice.trade_date >= from_date), count_stmt.where(DailyPrice.trade_date >= from_date)
    if to_date:
        stmt, count_stmt = stmt.where(DailyPrice.trade_date <= to_date), count_stmt.where(DailyPrice.trade_date <= to_date)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(stmt.order_by(DailyPrice.trade_date).limit(page.limit).offset(page.offset)).scalars().all()
    return Page(items=[PriceOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)
