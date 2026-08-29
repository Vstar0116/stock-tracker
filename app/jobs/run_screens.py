"""Evaluate every active screen against the latest trade_date and write
matches into `alerts`, each with a snapshot of the values that caused the
match. Idempotent: (screen_id, instrument_id, trade_date) is unique on
`alerts`, so re-running for a date that's already been screened inserts zero
new rows (ON CONFLICT DO NOTHING) rather than duplicating alerts.

Run with: python -m app.jobs.run_screens
"""

import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import Alert, Screen
from app.schemas.screen import parse_screen_definition
from app.services.screening import NON_SNAPSHOT_COLUMNS, compile_screen, latest_trade_date, previous_trade_date
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("run_screens")

JOB_NAME = "run_screens"


def evaluate_screen(db: Session, screen: Screen, as_of_date, prev_date) -> int:
    """Run one screen and upsert its matches. Returns the number of NEW
    alerts inserted (0 if the screen's definition is invalid, it needs a
    prior trading day that doesn't exist yet, or nothing matched)."""
    try:
        rule = parse_screen_definition(screen.definition)
    except ValidationError as exc:
        logger.error("screen %d (%s): invalid definition, skipping -- %s", screen.id, screen.name, exc)
        return 0

    stmt = compile_screen(rule, as_of_date, prev_date)
    if stmt is None:
        logger.info("screen %d (%s): no prior trading day yet, skipping", screen.id, screen.name)
        return 0

    rows = db.execute(stmt).mappings().all()
    if not rows:
        return 0

    values = [
        {
            "screen_id": screen.id,
            "instrument_id": row["instrument_id"],
            "trade_date": row["trade_date"],
            "snapshot": {
                k: (float(v) if v is not None and not isinstance(v, str) else v)
                for k, v in row.items()
                if k not in NON_SNAPSHOT_COLUMNS
            },
        }
        for row in rows
    ]

    stmt = pg_insert(Alert).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=[Alert.screen_id, Alert.instrument_id, Alert.trade_date])
    result = db.execute(stmt)
    return result.rowcount


def run() -> int:
    db = SessionLocal()
    total_matches = 0
    screens_evaluated = 0
    try:
        with track_job_run(db, JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            as_of_date = latest_trade_date(db)
            if as_of_date is None:
                logger.info("no price data loaded yet -- nothing to screen")
            else:
                prev_date = previous_trade_date(db, as_of_date)
                screens = db.execute(select(Screen).where(Screen.is_active.is_(True))).scalars().all()
                for screen in screens:
                    count = evaluate_screen(db, screen, as_of_date, prev_date)
                    total_matches += count
                    screens_evaluated += 1
                    logger.info("screen %d (%s): %d new matches on %s", screen.id, screen.name, count, as_of_date)

            tracker.rows_processed = total_matches
            logger.info("evaluated %d screens, %d new alerts", screens_evaluated, total_matches)
    finally:
        db.close()
    return total_matches


def main() -> None:
    total = run()
    print(f"run_screens: {total} new alerts")


if __name__ == "__main__":
    main()
