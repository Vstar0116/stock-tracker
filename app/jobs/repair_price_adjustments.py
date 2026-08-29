"""One-off repair for daily_prices rows that a since-fixed race condition in
app/jobs/daily_pipeline.py::step_apply_corporate_actions left permanently
unadjusted.

Root cause: ingest_corporate_actions discovers a split/bonus up to 90 days
before its ex_date, and the old step_apply_corporate_actions applied (and
permanently marked "applied") the moment it was discovered -- a one-shot
UPDATE over whatever daily_prices rows existed for that instrument at that
instant. Any row for a date still before ex_date that got ingested *after*
that point (completely normal: trading days arrive one at a time) was never
touched, silently leaving a second, fake discontinuity in adjusted_close
right in the window recent indicators (SMA-20, RSI-14, ...) actually read.
The gate added to step_apply_corporate_actions (ex_date <= today before
applying) stops this from happening again; this script repairs the data it
already happened to.

Approach: for every instrument with at least one applied SPLIT/BONUS action,
recompute adjusted_close from the immutable `close` column as
close / PRODUCT(factor for every action where trade_date < action.ex_date),
rather than trying to detect which specific rows were "missed" -- that
sidesteps having to reason about partial application order across multiple
stacked actions on the same instrument (several in our data have a SPLIT and
a BONUS on the same ex_date) and makes this idempotent and safe to re-run:
a row already correctly adjusted recomputes to the same value and is a no-op.

Every instrument this touches gets a full indicator recompute afterward
(historical adjusted_close changed retroactively -- the normal incremental
compute_indicators run wouldn't know to revisit already-computed dates).

Run with: python -m app.jobs.repair_price_adjustments [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.compute_indicators import load_price_history, upsert_indicators
from app.models import CorporateAction, DailyPrice
from app.services.indicators import compute_all_indicators
from app.services.price_adjustment import ADJUSTABLE_ACTION_TYPES, adjustment_factor
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("repair_price_adjustments")


def _affected_instrument_ids(db: Session) -> list[int]:
    return list(
        db.execute(
            select(CorporateAction.instrument_id)
            .where(CorporateAction.applied.is_(True), CorporateAction.action_type.in_(ADJUSTABLE_ACTION_TYPES))
            .distinct()
        )
        .scalars()
        .all()
    )


def _correct_adjusted_close(row: DailyPrice, actions: list[CorporateAction]) -> Decimal:
    factor = Decimal(1)
    for action in actions:
        if row.trade_date < action.ex_date:
            factor *= adjustment_factor(action)
    return row.close / factor if factor != 1 else row.close


def repair_instrument(db: Session, instrument_id: int, dry_run: bool) -> int:
    actions = (
        db.execute(
            select(CorporateAction).where(
                CorporateAction.instrument_id == instrument_id,
                CorporateAction.applied.is_(True),
                CorporateAction.action_type.in_(ADJUSTABLE_ACTION_TYPES),
            )
        )
        .scalars()
        .all()
    )
    rows = db.execute(select(DailyPrice).where(DailyPrice.instrument_id == instrument_id)).scalars().all()

    fixed = 0
    for row in rows:
        correct = _correct_adjusted_close(row, actions)
        if row.adjusted_close != correct:
            fixed += 1
            if not dry_run:
                row.adjusted_close = correct
    if not dry_run and fixed:
        db.commit()
    return fixed


def _recompute_indicators(db: Session, instrument_id: int) -> int:
    prices = load_price_history(db, instrument_id)
    if prices.empty:
        return 0
    indicators_df = compute_all_indicators(prices)
    rows = upsert_indicators(db, instrument_id, indicators_df)
    db.commit()
    return rows


def run(dry_run: bool = False) -> dict[str, int]:
    db = SessionLocal()
    try:
        instrument_ids = _affected_instrument_ids(db)
        logger.info("%d instrument(s) have at least one applied split/bonus", len(instrument_ids))

        touched_instruments = 0
        rows_fixed = 0
        indicator_rows = 0
        for instrument_id in instrument_ids:
            fixed = repair_instrument(db, instrument_id, dry_run)
            if fixed:
                touched_instruments += 1
                rows_fixed += fixed
                logger.info("instrument %d: %d row(s) corrected", instrument_id, fixed)
                if not dry_run:
                    indicator_rows += _recompute_indicators(db, instrument_id)

        return {
            "instruments_checked": len(instrument_ids),
            "instruments_fixed": touched_instruments,
            "price_rows_fixed": rows_fixed,
            "indicator_rows_recomputed": indicator_rows,
        }
    finally:
        db.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Repair daily_prices rows missed by the corporate-action race")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
    args = parser.parse_args(argv)

    summary = run(dry_run=args.dry_run)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
