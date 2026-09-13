"""Independent "is the pipeline still alive" check -- plus the same watchdog
for the two manual, unscheduled data jobs (seed_bsgarp_fundamentals,
ingest_sector_classification) that daily_pipeline.py never calls.

This has to live outside daily_pipeline.py and run on its OWN schedule (see
deploy/systemd/stock-healthcheck.timer): if the thing that's supposed to
trigger the pipeline stops firing at all (a broken timer, a bad deploy, the
host down), nothing inside the pipeline can ever notice that -- only an
independent watcher checking "when did this last actually run" can.

The manual jobs have the same blind spot for a different reason: nothing
ever calls them again after the human who ran them once moves on, and there
is no error, no failed screen, nothing in the app itself that would ever
surface "this data is 8 months old" -- only job_runs' own history can.

Also covers the database-connection-failure alert for this job's own
startup, same reasoning as daily_pipeline.py's: if it can't even query
job_runs, that's alertable on its own, distinct from any one job being stale.

Run with: python -m app.jobs.healthcheck
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.jobs.ingest_sector_classification import JOB_NAME as SECTOR_JOB_NAME
from app.jobs.seed_bsgarp_fundamentals import JOB_NAME as FUNDAMENTALS_JOB_NAME
from app.models import JobRun
from app.services import alerting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("healthcheck")

# The pipeline runs once a day (weekdays); 36h covers a normal ~24h gap plus
# real slack for a delayed run, without waiting almost 2 full days to notice
# a genuinely dead scheduler.
STALE_AFTER = timedelta(hours=36)

# seed_bsgarp_fundamentals and ingest_sector_classification are both
# manual-entry jobs (CLAUDE.md: fundamentals are manual-entry only) with no
# timer of their own -- a human re-runs them, daily_pipeline never calls
# either. There's no "missed its slot" to detect the way there is for the
# pipeline, only "hasn't been touched in a long time". 200 days matches the
# Screener/Alerts UI's own "stale" badge threshold (frontend/src/lib/
# format.tsx's fundamentalsStaleness) so the UI badge and this ops alert
# agree on what "stale" means instead of using two different unexplained
# numbers.
MANUAL_DATA_STALE_AFTER = timedelta(days=200)


def _last_started(db: Session, job_name: str) -> datetime | None:
    return db.execute(select(func.max(JobRun.started_at)).where(JobRun.job_name == job_name)).scalar_one_or_none()


def _check_stale(db: Session, now: datetime, job_name: str, threshold: timedelta, subject: str, remediation: str) -> None:
    last_started = _last_started(db, job_name)
    age = None if last_started is None else now - last_started

    if age is None or age > threshold:
        last_run_desc = "never" if last_started is None else f"{last_started.isoformat()} ({age} ago)"
        logger.warning("healthcheck: %s stale -- last run %s", job_name, last_run_desc)
        alerting.send_alert(
            subject,
            f"Job: {job_name}\nLast attempted run: {last_run_desc}\n\n{remediation}",
            fingerprint=f"{job_name}:stale",
        )
    else:
        logger.info("healthcheck: %s last ran %s ago -- OK", job_name, age)


def run(now: datetime | None = None) -> None:
    """`now` defaults to the real current time; tests pass a fixed value so
    "is the last run stale" is deterministic regardless of whatever real
    job_runs rows already exist in the shared dev database."""
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        _check_stale(
            db, now, PIPELINE_JOB_NAME, STALE_AFTER,
            f"daily_pipeline has not run in over {int(STALE_AFTER.total_seconds() // 3600)} hours",
            "Check: systemctl status stock-daily-pipeline.timer\n"
            "Check: python -m app.jobs.daily_pipeline status",
        )
        _check_stale(
            db, now, FUNDAMENTALS_JOB_NAME, MANUAL_DATA_STALE_AFTER,
            f"BS-GARP fundamentals haven't been refreshed in over {MANUAL_DATA_STALE_AFTER.days} days",
            "Re-run with updated screener.in figures: python -m app.jobs.seed_bsgarp_fundamentals",
        )
        _check_stale(
            db, now, SECTOR_JOB_NAME, MANUAL_DATA_STALE_AFTER,
            f"Market-wide industry classification hasn't been refreshed in over {MANUAL_DATA_STALE_AFTER.days} days",
            "Re-run: python -m app.jobs.ingest_sector_classification",
        )
    except OperationalError as exc:
        alerting.send_alert(
            "healthcheck: database connection failed",
            f"Time: {now.isoformat()}\n"
            f"Could not connect to the database while checking pipeline health.\n\n"
            f"Error: {exc}",
            fingerprint="healthcheck:db_connection_failed",
        )
        logger.error("healthcheck: database connection failed: %s", exc)
    finally:
        db.close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
