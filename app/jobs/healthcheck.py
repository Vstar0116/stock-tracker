"""Independent "is the pipeline still alive" check.

This has to live outside daily_pipeline.py and run on its OWN schedule (see
deploy/systemd/stock-healthcheck.timer): if the thing that's supposed to
trigger the pipeline stops firing at all (a broken timer, a bad deploy, the
host down), nothing inside the pipeline can ever notice that -- only an
independent watcher checking "when did this last actually run" can.

Also covers the database-connection-failure alert for this job's own
startup, same reasoning as daily_pipeline.py's: if it can't even query
job_runs, that's alertable on its own, distinct from "pipeline hasn't run".

Run with: python -m app.jobs.healthcheck
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.models import JobRun
from app.services import alerting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("healthcheck")

# The pipeline runs once a day (weekdays); 36h covers a normal ~24h gap plus
# real slack for a delayed run, without waiting almost 2 full days to notice
# a genuinely dead scheduler.
STALE_AFTER = timedelta(hours=36)


def run(now: datetime | None = None) -> None:
    """`now` defaults to the real current time; tests pass a fixed value so
    "is the last run stale" is deterministic regardless of whatever real
    job_runs rows already exist in the shared dev database."""
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        last_started = db.execute(
            select(func.max(JobRun.started_at)).where(JobRun.job_name == PIPELINE_JOB_NAME)
        ).scalar_one_or_none()
    except OperationalError as exc:
        alerting.send_alert(
            "healthcheck: database connection failed",
            f"Time: {now.isoformat()}\n"
            f"Could not connect to the database while checking pipeline health.\n\n"
            f"Error: {exc}",
            fingerprint="healthcheck:db_connection_failed",
        )
        logger.error("healthcheck: database connection failed: %s", exc)
        return
    finally:
        db.close()

    age = None if last_started is None else now - last_started

    if age is None or age > STALE_AFTER:
        last_run_desc = "never" if last_started is None else f"{last_started.isoformat()} ({age} ago)"
        logger.warning("healthcheck: pipeline stale -- last run %s", last_run_desc)
        alerting.send_alert(
            f"daily_pipeline has not run in over {int(STALE_AFTER.total_seconds() // 3600)} hours",
            f"Job: {PIPELINE_JOB_NAME}\n"
            f"Last attempted run: {last_run_desc}\n\n"
            f"Check: systemctl status stock-daily-pipeline.timer\n"
            f"Check: python -m app.jobs.daily_pipeline status",
            fingerprint="daily_pipeline:stale",
        )
    else:
        logger.info("healthcheck: pipeline last ran %s ago -- OK", age)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
