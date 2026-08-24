"""Custom MA crossover: view on one instrument (instant) and scan the whole
market (Task 5, a few seconds). Additive to the existing indicator/screener
features -- see docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md.
"""

import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.compute_indicators import load_price_history
from app.models import Instrument
from app.schemas.crossover import CrossoverPoint, CrossoverSeriesOut, MaType
from app.services.crossover import compute_crossover, validate_periods

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
