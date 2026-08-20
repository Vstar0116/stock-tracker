"""Shared job_runs bookkeeping for the common shape every job in this package
follows: do the work, record a "success" row with a row count, or on any
exception roll back, record a "failed" row with the error, and re-raise.

Not every job uses this -- app/jobs/ingest_prices.py's run_for_exchange() has
a genuinely different shape (a third "skipped" outcome for holidays, and it
returns a status instead of raising so callers can inspect both exchanges
before deciding whether to fail) -- that's a real branching difference, not
just duplicated code, so it keeps its own bookkeeping.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models import JobRun


class JobRunTracker:
    rows_processed: int = 0


@contextmanager
def track_job_run(db: Session, job_name: str, run_date: date):
    """Yields a tracker whose .rows_processed you can set before the block
    ends. Commits a "success" JobRun row with that count on a clean exit, or
    rolls back and commits a "failed" row (error message truncated to fit the
    column) before re-raising on any exception."""
    started_at = datetime.now(timezone.utc)
    tracker = JobRunTracker()
    try:
        yield tracker
    except Exception as exc:
        db.rollback()
        db.add(
            JobRun(
                job_name=job_name,
                run_date=run_date,
                status="failed",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                rows_processed=0,
                error_message=str(exc)[:2000],
            )
        )
        db.commit()
        raise
    else:
        db.add(
            JobRun(
                job_name=job_name,
                run_date=run_date,
                status="success",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                rows_processed=tracker.rows_processed,
            )
        )
        db.commit()
