"""Price CAGR over fixed lookback windows, computed from DailyPrice.adjusted_close
-- already fully stored (corporate-action adjusted), so this is pure computation,
no new ingestion.

Run with: N/A -- called from app/api/instruments.py, not a standalone job.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyPrice

CAGR_HORIZON_YEARS = (1, 3, 5, 10)

# A start price must land within this many days of the exact N-years-back
# target date, or the window is untrustworthy -- e.g. an instrument listed 6
# years ago has no real 10yr price, so its "10yr CAGR" must come back None,
# not get silently computed over 6 years and mislabeled as 10.
STALE_TOLERANCE_DAYS = 30

DAYS_PER_YEAR = 365.25


def _pure_cagr(latest: Decimal, start: Decimal, elapsed_years: float) -> float | None:
    """(latest/start)^(1/elapsed_years) - 1, as a fraction (0.12 = 12%/yr).
    None if start isn't positive (can't ratio against it) or there's no
    real elapsed time."""
    if start <= 0 or elapsed_years <= 0:
        return None
    return (float(latest) / float(start)) ** (1 / elapsed_years) - 1


def _nearest_price_on_or_before(db: Session, instrument_id: int, target_date: date):
    return db.execute(
        select(DailyPrice.trade_date, DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == instrument_id, DailyPrice.trade_date <= target_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    ).first()


def instrument_cagrs(
    db: Session, instrument_id: int, latest_date: date, latest_close: Decimal
) -> dict[int, float | None]:
    """years -> CAGR (or None if there's not enough trustworthy history) for
    every horizon in CAGR_HORIZON_YEARS."""
    result: dict[int, float | None] = {}
    for years in CAGR_HORIZON_YEARS:
        target = latest_date - timedelta(days=round(DAYS_PER_YEAR * years))
        row = _nearest_price_on_or_before(db, instrument_id, target)
        if row is None or (target - row.trade_date).days > STALE_TOLERANCE_DAYS:
            result[years] = None
            continue
        elapsed_years = (latest_date - row.trade_date).days / DAYS_PER_YEAR
        result[years] = _pure_cagr(latest_close, row.adjusted_close, elapsed_years)
    return result
