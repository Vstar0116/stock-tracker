"""Custom MA crossover: view on one instrument (instant) and scan the whole
market (Task 5, a few seconds). Additive to the existing indicator/screener
features -- see docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md.
"""

import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.compute_indicators import load_price_history
from app.models import DailyPrice, Instrument, PortfolioReport, PortfolioReportItem, User, Watchlist, WatchlistItem
from app.schemas.crossover import (
    CrossoverPoint,
    CrossoverSeriesOut,
    MaType,
    ScanMatchOut,
    ScanRequest,
    ScanResponse,
    ScanStats,
)
from app.services.crossover import compute_crossover, validate_periods
from app.services.crossover_loader import run_scan
from app.services.portfolio_reports import matched_instrument_ids

router = APIRouter(prefix="/api", tags=["crossover"], dependencies=[Depends(get_current_user)])


def _none_if_nan(v: float) -> float | None:
    return None if v is None or math.isnan(v) else float(v)


def _signal_or_none(v: object) -> str | None:
    # compute_crossover's `signal` column is object-dtype but pandas stores
    # the "no crossing" cells as float nan rather than None (a pd.Series(None,
    # dtype=object) construction quirk in app/services/crossover.py) -- so
    # `is None` doesn't catch it. Anything that isn't the expected str is
    # treated as "no signal".
    return v if isinstance(v, str) else None


@router.get("/instruments/{instrument_id}/crossover", response_model=CrossoverSeriesOut)
def get_crossover(
    instrument_id: int,
    fast: int = Query(...),
    slow: int = Query(...),
    ma_type: MaType = Query(...),
    db: Session = Depends(get_db),
) -> CrossoverSeriesOut:
    if db.get(Instrument, instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")
    try:
        validate_periods(fast, slow)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    prices = load_price_history(db, instrument_id)
    if prices.empty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no price history for this instrument")

    result = compute_crossover(prices, fast, slow, ma_type)
    points = [
        CrossoverPoint(
            trade_date=trade_date,
            fast=_none_if_nan(row["fast"]),
            slow=_none_if_nan(row["slow"]),
            signal=_signal_or_none(row["signal"]),
        )
        for trade_date, row in result.iterrows()
    ]
    return CrossoverSeriesOut(instrument_id=instrument_id, fast=fast, slow=slow, ma_type=ma_type, points=points)


def _restriction_set(db: Session, current_user: User, payload: ScanRequest) -> frozenset[int] | None:
    """Builds the instrument-id subset ScanRequest.report_id/watchlist_only
    restrict a scan to, or None for the default whole-market scan. Ownership
    of report_id is checked here (not via the get_owned_report dependency --
    this is a field on a POST body, not a path param) the same
    ownership-in-the-WHERE-clause way as everywhere else: someone else's
    report_id 404s instead of leaking whether it exists."""
    restriction: frozenset[int] | None = None

    if payload.report_id is not None:
        report = db.execute(
            select(PortfolioReport).where(
                PortfolioReport.id == payload.report_id, PortfolioReport.user_id == current_user.id
            )
        ).scalar_one_or_none()
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
        restriction = matched_instrument_ids(db, report.id)

    if payload.watchlist_only:
        watchlist_ids = frozenset(
            db.execute(
                select(WatchlistItem.instrument_id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(Watchlist.user_id == current_user.id)
            )
            .scalars()
            .all()
        )
        restriction = watchlist_ids if restriction is None else (restriction & watchlist_ids)

    return restriction


@router.post("/scans/crossover", response_model=ScanResponse)
def scan_crossover(
    payload: ScanRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ScanResponse:
    restriction = _restriction_set(db, current_user, payload)
    result = run_scan(db, payload.fast, payload.slow, payload.ma_type, payload.direction, restriction)

    instrument_ids = list(result.matches.index)
    rows = {}
    if instrument_ids:
        # LEFT OUTER JOIN from Instrument, not an inner join: load_wide
        # (crossover_loader) deliberately forward-fills short gaps within
        # STALE_TOLERANCE_DAYS, so a match can legitimately have no
        # DailyPrice row on the exact as_of date (trading halt, illiquid
        # smallcap with a sparse bhavcopy). An inner join here would
        # silently drop those matches after the scan already found them --
        # canceling the point of the forward-fill tolerance. A missing
        # DailyPrice row now just yields latest_close=None instead.
        for inst, close in db.execute(
            select(Instrument, DailyPrice.adjusted_close)
            .outerjoin(DailyPrice, (DailyPrice.instrument_id == Instrument.id) & (DailyPrice.trade_date == result.as_of))
            .where(Instrument.id.in_(instrument_ids))
        ).all():
            rows[inst.id] = (inst, close)

    # The scanned report's own recorded values per ticker (Group/Score/PDF
    # price/Zone), so the results table can show them alongside today's
    # live signal -- empty, and every pdf_* field below stays None, unless
    # report_id was actually set.
    pdf_items: dict[int, PortfolioReportItem] = {}
    if payload.report_id is not None and instrument_ids:
        pdf_items = {
            item.instrument_id: item
            for item in db.execute(
                select(PortfolioReportItem).where(
                    PortfolioReportItem.report_id == payload.report_id,
                    PortfolioReportItem.instrument_id.in_(instrument_ids),
                )
            ).scalars()
        }

    matches = [
        ScanMatchOut(
            instrument_id=instrument_id,
            symbol=rows[instrument_id][0].symbol,
            exchange=rows[instrument_id][0].exchange,
            sector=rows[instrument_id][0].sector,
            latest_close=float(rows[instrument_id][1]) if rows[instrument_id][1] is not None else None,
            signal=signal,
            pdf_group=pdf_items[instrument_id].grp if instrument_id in pdf_items else None,
            pdf_score=pdf_items[instrument_id].score if instrument_id in pdf_items else None,
            pdf_price=float(pdf_items[instrument_id].pdf_price)
            if instrument_id in pdf_items and pdf_items[instrument_id].pdf_price is not None
            else None,
            pdf_zone=pdf_items[instrument_id].zone if instrument_id in pdf_items else None,
        )
        for instrument_id, signal in result.matches.items()
        # Genuine can't-happen guard only: an Instrument row disappearing
        # between the scan and this hydration query (e.g. deleted
        # concurrently). Not a way to filter out legitimately-matched,
        # not-priced-today instruments -- the outer join above already
        # keeps those, with latest_close=None.
        if instrument_id in rows
    ]

    return ScanResponse(
        as_of=result.as_of,
        params=payload,
        stats=ScanStats(
            evaluated=result.evaluated,
            matched=len(matches),
            skipped_insufficient_history=result.skipped_insufficient_history,
            skipped_stale=result.skipped_stale,
            elapsed_ms=result.elapsed_ms,
            cached=result.cached,
            universe=result.universe,
        ),
        matches=matches,
    )
