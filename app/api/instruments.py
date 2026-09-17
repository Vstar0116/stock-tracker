from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import Pagination, get_current_user, pagination
from app.db.session import get_db
from app.models import CorporateAction, DailyPrice, Fundamental, Indicator, Instrument
from app.schemas.common import Page
from app.schemas.instrument import IndicatorOut, InstrumentDetail, InstrumentOut, PeerRow, PriceOut
from app.services.cagr import instrument_cagrs

router = APIRouter(prefix="/api/instruments", tags=["instruments"], dependencies=[Depends(get_current_user)])


def _tv_symbol(db: Session, instrument: Instrument) -> str:
    """TradingView's free widget covers NSE far more completely than BSE --
    most BSE small/micro-caps 404 there even with the correct numeric scrip
    code. Prefer the NSE listing of the same company (matched by ISIN) when
    one exists; only fall back to the BSE line if this instrument has no
    NSE-listed sibling."""
    if instrument.exchange != "BSE" or not instrument.isin:
        return f"{instrument.exchange}:{instrument.symbol}"

    nse_sibling = db.execute(
        select(Instrument.symbol).where(
            Instrument.isin == instrument.isin, Instrument.exchange == "NSE", Instrument.is_active
        )
    ).scalar_one_or_none()
    if nse_sibling:
        return f"NSE:{nse_sibling}"
    return f"BSE:{instrument.bse_scrip_code}" if instrument.bse_scrip_code else f"BSE:{instrument.symbol}"


def _dividend_yields(db: Session, instrument_ids: list[int]) -> dict[int, float]:
    """Trailing-12-month dividend total per instrument (sum of DIVIDEND
    `value` rows -- see ingest_corporate_actions.py -- with ex_date in the
    last 365 days). Callers divide by their own latest_close and *100; kept
    as a raw sum here since get_instrument and get_instrument_peers each
    already have latest_close from a different query."""
    if not instrument_ids:
        return {}
    cutoff = date.today() - timedelta(days=365)
    rows = db.execute(
        select(CorporateAction.instrument_id, func.sum(CorporateAction.value))
        .where(
            CorporateAction.instrument_id.in_(instrument_ids),
            CorporateAction.action_type == "DIVIDEND",
            CorporateAction.ex_date > cutoff,
        )
        .group_by(CorporateAction.instrument_id)
    ).all()
    return {instrument_id: float(total) for instrument_id, total in rows}


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

    dividend_total = _dividend_yields(db, [instrument_id]).get(instrument_id)
    dividend_yield = dividend_total / latest_close * 100 if dividend_total is not None and latest_close else None

    cagrs = (
        instrument_cagrs(db, instrument_id, latest_trade_date, last_two_prices[0].adjusted_close)
        if latest_trade_date
        else {}
    )

    return InstrumentDetail(
        id=instrument.id,
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        bse_scrip_code=instrument.bse_scrip_code,
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
        dividend_yield=dividend_yield,
        cagr_1y=cagrs.get(1),
        cagr_3y=cagrs.get(3),
        cagr_5y=cagrs.get(5),
        cagr_10y=cagrs.get(10),
        tv_symbol=_tv_symbol(db, instrument),
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


@router.get("/{instrument_id}/indicators", response_model=Page[IndicatorOut])
def get_instrument_indicators(
    instrument_id: int,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
) -> Page[IndicatorOut]:
    """Per-day indicator series -- compute_indicators.py already stores one
    row per (instrument, trade_date); this is the first endpoint to serve
    them as a series rather than just the latest row (see latest_indicators
    on InstrumentDetail). Lets the frontend plot SMA/EMA overlays itself
    instead of relying on TradingView's free-tier study cap."""
    if db.get(Instrument, instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    stmt = select(Indicator).where(Indicator.instrument_id == instrument_id)
    count_stmt = select(func.count()).select_from(Indicator).where(Indicator.instrument_id == instrument_id)
    if from_date:
        stmt, count_stmt = stmt.where(Indicator.trade_date >= from_date), count_stmt.where(Indicator.trade_date >= from_date)
    if to_date:
        stmt, count_stmt = stmt.where(Indicator.trade_date <= to_date), count_stmt.where(Indicator.trade_date <= to_date)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(stmt.order_by(Indicator.trade_date).limit(page.limit).offset(page.offset)).scalars().all()
    return Page(items=[IndicatorOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.get("/{instrument_id}/peers", response_model=list[PeerRow])
def get_instrument_peers(instrument_id: int, db: Session = Depends(get_db)) -> list[PeerRow]:
    """Peers grouped by NSE/BSE `industry` when available, falling back to
    the curated sunrise `sector` when `industry` is missing (see
    app/jobs/ingest_sector_classification.py) -- widens the pool beyond the
    old sector-only join without needing a new manually-curated grouping.
    Peers are still capped by who has a fundamentals row (manual-entry only),
    so the peer set is never the full market. An instrument with neither
    industry nor sector, or no peer with a fundamentals row (including
    itself), yields [] so the frontend section stays hidden rather than
    rendering an empty table."""
    instrument = db.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")
    group_key = instrument.industry or instrument.sector
    if group_key is None:
        return []

    # DISTINCT ON pattern matches _latest_fundamentals_subquery in
    # app/services/screening.py: one row per instrument, its most recent
    # fundamentals snapshot.
    fund_sub = (
        select(Fundamental)
        .distinct(Fundamental.instrument_id)
        .order_by(Fundamental.instrument_id, Fundamental.as_of_date.desc())
        .subquery("peer_fund")
    )
    fund = aliased(Fundamental, fund_sub)

    price_sub = (
        select(DailyPrice.instrument_id, DailyPrice.adjusted_close)
        .distinct(DailyPrice.instrument_id)
        .order_by(DailyPrice.instrument_id, DailyPrice.trade_date.desc())
        .subquery("peer_price")
    )

    rows = db.execute(
        select(Instrument, fund, price_sub.c.adjusted_close)
        .join(fund, fund.instrument_id == Instrument.id)
        .outerjoin(price_sub, price_sub.c.instrument_id == Instrument.id)
        .where(func.coalesce(Instrument.industry, Instrument.sector) == group_key)
        .order_by(Instrument.symbol)
    ).all()

    dividend_yields = _dividend_yields(db, [inst.id for inst, _, _ in rows])

    return [
        PeerRow(
            instrument_id=inst.id,
            symbol=inst.symbol,
            company_name=inst.company_name,
            cmp=float(cmp_) if cmp_ is not None else None,
            market_cap=float(f.market_cap) if f.market_cap is not None else None,
            pe=float(f.pe) if f.pe is not None else None,
            roce=float(f.roce) if f.roce is not None else None,
            debt_to_equity=float(f.debt_to_equity) if f.debt_to_equity is not None else None,
            peg=float(f.peg) if f.peg is not None else None,
            eps_diluted=float(f.eps_diluted) if f.eps_diluted is not None else None,
            eps_growth=float(f.eps_growth) if f.eps_growth is not None else None,
            fcf_per_share=float(f.fcf_per_share) if f.fcf_per_share is not None else None,
            fcf_conversion=float(f.fcf_conversion) if f.fcf_conversion is not None else None,
            roe=float(f.roe) if f.roe is not None else None,
            debtor_days=float(f.debtor_days) if f.debtor_days is not None else None,
            payable_days=float(f.payable_days) if f.payable_days is not None else None,
            inventory_days=float(f.inventory_days) if f.inventory_days is not None else None,
            cash_conversion_cycle=float(f.cash_conversion_cycle) if f.cash_conversion_cycle is not None else None,
            working_capital_days=float(f.working_capital_days) if f.working_capital_days is not None else None,
            dividend_yield=(
                dividend_yields[inst.id] / float(cmp_) * 100
                if inst.id in dividend_yields and cmp_ is not None
                else None
            ),
            fundamentals_as_of=f.as_of_date,
        )
        for inst, f, cmp_ in rows
    ]
