"""Backfill NSE/BSE daily bhavcopy history into `daily_prices` over a date range.

Reuses app.jobs.ingest_prices.run_for_exchange for the actual fetch/parse/load/
job_runs logic -- this script only walks the date range, skips days already
recorded in job_runs, and paces the requests.

Run with: python -m app.jobs.backfill_prices --from 2024-01-01 --to 2024-12-31
"""

import argparse
import logging
import time
from datetime import datetime, timedelta
from datetime import date as date_cls

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.ingest_prices import fetch_bse_bhavcopy, fetch_nse_bhavcopy, run_for_exchange
from app.models import JobRun
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("backfill_prices")

EXCHANGES = (
    ("NSE", fetch_nse_bhavcopy),
    ("BSE", fetch_bse_bhavcopy),
)

DEFAULT_PAUSE_SECONDS = 1.5


def already_done(db: Session, job_name: str, run_date: date_cls) -> bool:
    """A prior run already recorded a non-failed outcome for this (job, date) --
    nothing to redo, so a resumed backfill can skip it without hitting the network."""
    return (
        db.execute(
            select(JobRun.id)
            .where(
                JobRun.job_name == job_name,
                JobRun.run_date == run_date,
                JobRun.status.in_(("success", "skipped")),
            )
            .limit(1)
        ).first()
        is not None
    )


def backfill(start: date_cls, end: date_cls, pause_seconds: float) -> None:
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    total_days = len(dates)
    total_rows = 0

    db = SessionLocal()
    try:
        for day_index, trade_date in enumerate(dates, start=1):
            parts = []
            for exchange, fetch_fn in EXCHANGES:
                job_name = f"ingest_prices_{exchange.lower()}"

                if already_done(db, job_name, trade_date):
                    parts.append(f"{exchange}=already-loaded")
                    continue

                status, rows = run_for_exchange(db, exchange, trade_date, fetch_fn)
                total_rows += rows
                parts.append(f"{exchange}={status}({rows} rows)")
                time.sleep(pause_seconds)  # be polite -- only after a call that actually hit the network

            logger.info("[%d/%d] %s: %s", day_index, total_days, trade_date, ", ".join(parts))
    finally:
        db.close()

    logger.info("Backfill done: %d days processed, %d total rows loaded", total_days, total_rows)


def parse_args(argv=None) -> tuple[date_cls, date_cls, float]:
    parser = argparse.ArgumentParser(description="Backfill NSE/BSE daily bhavcopy history into daily_prices")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date, inclusive, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End date, inclusive, YYYY-MM-DD")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help=f"Pause between exchange requests (default: {DEFAULT_PAUSE_SECONDS}s)",
    )
    args = parser.parse_args(argv)

    def _parse_date(raw: str, flag: str) -> date_cls:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"{flag} must be YYYY-MM-DD")

    start = _parse_date(args.from_date, "--from")
    end = _parse_date(args.to_date, "--to")
    if start > end:
        parser.error("--from must not be after --to")
    return start, end, args.pause_seconds


def main(argv=None) -> None:
    start, end, pause_seconds = parse_args(argv)
    backfill(start, end, pause_seconds)


if __name__ == "__main__":
    main()
