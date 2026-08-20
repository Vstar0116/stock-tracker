"""Run the full daily ingest -> adjust -> compute -> screen pipeline in order.

Each step below delegates to that job's own module, which already opens its
own session, commits, and writes its own job_runs row (success/failed) --
this file only sequences them, stops at the first failure, and writes one
extra job_runs row (job_name="daily_pipeline") summarizing the whole run.

Every step is individually idempotent (upserts / ON CONFLICT DO NOTHING /
"already applied" claims), so re-running this script after a failure is safe:
completed steps do cheap no-op work, the failed step (and everything after
it) runs for real.

Run with:
    python -m app.jobs.daily_pipeline            # run the pipeline
    python -m app.jobs.daily_pipeline --dry-run   # report what would happen, write nothing
    python -m app.jobs.daily_pipeline status      # show last run of each job + data freshness
"""

import argparse
import logging
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal
from app.jobs import compute_indicators, ingest_corporate_actions, ingest_instruments, ingest_prices, run_screens
from app.jobs._tracking import track_job_run
from app.jobs.ingest_prices import IST_OFFSET, most_recent_trading_day
from app.models import CorporateAction, DailyPrice, JobRun, Screen
from app.services import alerting
from app.services.price_adjustment import apply_corporate_action

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_pipeline")

PIPELINE_JOB_NAME = "daily_pipeline"
APPLY_ACTIONS_JOB_NAME = "apply_corporate_actions"

INSTRUMENTS_REFRESH_DAYS = 7  # symbol master changes rarely -- no need to hit it every day

# The market's own calendar, not whatever timezone the host happens to be in
# -- a server on UTC is on a different calendar date from IST for part of
# every day, and IST has no DST so this is exact (no offset arithmetic).
IST = ZoneInfo("Asia/Kolkata")

# Belt-and-suspenders retry around the WHOLE pipeline (distinct from
# ingest_prices.py's own short retry for "bhavcopy not published in the last
# few seconds") -- covers a transient failure anywhere in the chain: a
# network blip, NSE/BSE briefly unreachable, a DB hiccup. Every step is
# idempotent (see run_pipeline's docstring), so re-running the full pipeline
# on failure is safe -- completed steps are cheap no-ops, only the failed
# step (and anything after it) does real work again.
#
# 3 attempts, 5 then 15 minutes apart: long enough for a short outage to
# clear, short enough that even a worst-case run (~25 min of waiting) still
# finishes hours before the next trading day.
PIPELINE_MAX_ATTEMPTS = 3
PIPELINE_RETRY_BACKOFF_SECONDS = (300, 900)

# Arbitrary fixed key for this job's Postgres advisory lock. pg_advisory_lock
# keys share one flat namespace per database -- fine today since nothing else
# in this app takes one, but if that changes, keys must not collide.
PIPELINE_LOCK_KEY = 827364591


def _last_success_date(job_name: str) -> date | None:
    db = SessionLocal()
    try:
        return db.execute(
            select(func.max(JobRun.run_date)).where(JobRun.job_name == job_name, JobRun.status == "success")
        ).scalar_one_or_none()
    finally:
        db.close()


def step_ingest_instruments(dry_run: bool) -> str:
    last = _last_success_date(ingest_instruments.JOB_NAME)
    if last is not None and last >= date.today() - timedelta(days=INSTRUMENTS_REFRESH_DAYS):
        return f"skipped -- last successful run {last}, within {INSTRUMENTS_REFRESH_DAYS} days"
    if dry_run:
        return "would run -- no successful run in the last " + f"{INSTRUMENTS_REFRESH_DAYS} days"
    counts = ingest_instruments.run()
    return f"ran -- {counts}"


def step_ingest_corporate_actions(dry_run: bool) -> str:
    from_date, to_date = date.today(), date.today() + timedelta(days=90)
    if dry_run:
        return f"would fetch announcements for {from_date}..{to_date}"
    counts = ingest_corporate_actions.run(from_date, to_date)
    return f"ran -- {counts}"


def step_ingest_prices(dry_run: bool) -> str:
    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
    trade_date = most_recent_trading_day(today_ist)
    if dry_run:
        return f"would ingest NSE+BSE bhavcopy for {trade_date}"
    ingest_prices.run(trade_date)
    return f"ran -- ingested {trade_date}"


def step_apply_corporate_actions(dry_run: bool) -> str:
    db = SessionLocal()
    try:
        pending = db.execute(select(CorporateAction).where(CorporateAction.applied.is_(False))).scalars().all()
        if dry_run:
            return f"would apply {len(pending)} pending corporate action(s)"
        if not pending:
            return "nothing pending"

        with track_job_run(db, APPLY_ACTIONS_JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            for action in pending:
                tracker.rows_processed += apply_corporate_action(db, action)  # commits per-action, see price_adjustment.py
        return f"ran -- applied {len(pending)} action(s), {tracker.rows_processed} price rows adjusted"
    finally:
        db.close()


def step_compute_indicators(dry_run: bool) -> str:
    if dry_run:
        db = SessionLocal()
        try:
            targets = compute_indicators.instruments_needing_recompute(db)
        finally:
            db.close()
        return f"would recompute indicators for {len(targets)} instrument(s)"
    total_rows = compute_indicators.run()
    return f"ran -- {total_rows} rows upserted"


def step_run_screens(dry_run: bool) -> str:
    if dry_run:
        db = SessionLocal()
        try:
            n = db.execute(select(func.count()).select_from(Screen).where(Screen.is_active.is_(True))).scalar_one()
        finally:
            db.close()
        return f"would evaluate {n} active screen(s)"
    total = run_screens.run()
    return f"ran -- {total} new alerts"


STEPS = [
    ("ingest_instruments", step_ingest_instruments),
    ("ingest_corporate_actions", step_ingest_corporate_actions),
    ("ingest_prices", step_ingest_prices),
    ("apply_corporate_actions", step_apply_corporate_actions),
    ("compute_indicators", step_compute_indicators),
    ("run_screens", step_run_screens),
]


def run_pipeline(dry_run: bool = False) -> None:
    logger.info("=== daily pipeline start%s ===", " (dry run)" if dry_run else "")

    if dry_run:
        for name, step_fn in STEPS:
            t0 = time.perf_counter()
            summary = step_fn(dry_run)
            logger.info("[%s] %s (%.1fs)", name, summary, time.perf_counter() - t0)
        logger.info("=== daily pipeline done ===")
        return

    db = SessionLocal()
    try:
        with track_job_run(db, PIPELINE_JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            for name, step_fn in STEPS:
                t0 = time.perf_counter()
                try:
                    summary = step_fn(dry_run)
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    logger.error("[%s] FAILED after %.1fs: %s", name, elapsed, exc)
                    raise RuntimeError(f"step {name!r} failed: {exc}") from exc
                logger.info("[%s] %s (%.1fs)", name, summary, time.perf_counter() - t0)
            tracker.rows_processed = len(STEPS)
    finally:
        db.close()
    logger.info("=== daily pipeline done ===")


@contextmanager
def _pipeline_lock():
    """Postgres session-scoped advisory lock: at most one daily_pipeline run
    at a time, across however many processes/hosts talk to this database.
    Non-blocking -- a second concurrent invocation (e.g. someone manually
    re-running it while the scheduled run is still going) sees the lock held
    and exits immediately rather than queueing behind it or racing it.

    Self-healing if a prior run crashed without cleanup: Postgres releases a
    session-scoped advisory lock automatically when its connection closes, so
    there's no stuck-lock row to notice and manually clear.
    """
    db = SessionLocal()
    try:
        acquired = db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": PIPELINE_LOCK_KEY}).scalar_one()
        try:
            yield acquired
        finally:
            if acquired:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": PIPELINE_LOCK_KEY})
    finally:
        db.close()


def _check_row_count(trade_date: date) -> None:
    """Alert if today's ingest looks suspiciously thin -- catches a partial
    ingest (e.g. one exchange silently returned near-nothing, or most
    symbols failed to match) that finishes as a "success" with real but
    tiny row counts, which a plain failure alert would never catch.

    Threshold: NSE's own EQ-series bhavcopy alone is normally ~2,500-2,600
    rows (2,541 observed on a normal day this session; BSE typically adds
    several thousand more on top -- 6,999 combined, same day). Day-to-day
    variance in which symbols actually traded is small, well under 10%.
    1,000 -- under half of NSE's contribution ALONE -- is comfortably below
    any normal day's total and comfortably above zero, so tripping it means
    something broke, not that today was a quiet trading day.
    """
    MIN_EXPECTED_DAILY_PRICE_ROWS = 1000
    db = SessionLocal()
    try:
        count = db.execute(
            select(func.count()).select_from(DailyPrice).where(DailyPrice.trade_date == trade_date)
        ).scalar_one()
    finally:
        db.close()

    if count < MIN_EXPECTED_DAILY_PRICE_ROWS:
        logger.warning("daily_pipeline: only %d daily_prices rows for %s -- suspiciously low", count, trade_date)
        alerting.send_alert(
            "daily_pipeline: suspiciously few rows ingested",
            f"Only {count} daily_prices rows for {trade_date} (expected at least "
            f"{MIN_EXPECTED_DAILY_PRICE_ROWS} on a normal trading day -- NSE alone is "
            f"normally ~2,500+). The pipeline reported success, but this looks like a "
            f"partial ingest.\n\nCheck: python -m app.jobs.daily_pipeline status",
            fingerprint="daily_pipeline:low_row_count",
        )


def run_pipeline_with_retries() -> bool:
    """Entry point for the scheduler (see deploy/systemd/). Skips weekends
    (IST calendar, not the host's), takes the advisory lock, then runs the
    pipeline with retry-with-backoff on failure. Exchange holidays are NOT
    handled here -- ingest_prices.py already treats "bhavcopy not published"
    as a clean skip, not a failure, so a holiday run finishes as a normal
    success with nothing new to ingest. Returns True if the pipeline
    ultimately succeeded (or there was nothing to do), False otherwise.

    Each attempt calls run_pipeline(), which writes its own job_runs row via
    track_job_run -- so a day that took 2 tries shows 1 failed + 1 success
    row, not a single row hiding the retry. Only ONE alert is sent per
    invocation regardless of how many attempts it took -- alerting fires
    once, after retries are exhausted (or once for the low-row-count check,
    after an eventual success), never once per attempt.
    """
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info("daily_pipeline: %s is a weekend in IST -- skipping", now_ist.date())
        return True

    with _pipeline_lock() as acquired:
        if not acquired:
            logger.warning("daily_pipeline: another run is already in progress -- exiting")
            return False

        last_exc: Exception | None = None
        for attempt in range(1, PIPELINE_MAX_ATTEMPTS + 1):
            try:
                run_pipeline(dry_run=False)
                _check_row_count(most_recent_trading_day(now_ist.date()))
                return True
            except Exception as exc:
                last_exc = exc
                if attempt == PIPELINE_MAX_ATTEMPTS:
                    break
                delay = PIPELINE_RETRY_BACKOFF_SECONDS[attempt - 1]
                logger.warning(
                    "daily_pipeline: attempt %d/%d failed (%s) -- retrying in %ds",
                    attempt, PIPELINE_MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)

        logger.error("daily_pipeline: giving up after %d attempts: %s", PIPELINE_MAX_ATTEMPTS, last_exc)
        alerting.send_alert(
            "daily_pipeline failed",
            f"Job: {PIPELINE_JOB_NAME}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}\n"
            f"Attempts: {PIPELINE_MAX_ATTEMPTS} (all failed)\n"
            f"Error: {last_exc}\n\n"
            f"Check: python -m app.jobs.daily_pipeline status",
            fingerprint="daily_pipeline:failed",
        )
    return False


STATUS_JOB_NAMES = [
    ingest_instruments.JOB_NAME,
    ingest_corporate_actions.JOB_NAME,
    "ingest_prices_nse",
    "ingest_prices_bse",
    APPLY_ACTIONS_JOB_NAME,
    compute_indicators.JOB_NAME,
    run_screens.JOB_NAME,
    PIPELINE_JOB_NAME,
]


def print_status() -> None:
    db = SessionLocal()
    try:
        print(f"{'job':<26}{'status':<10}{'run_date':<12}{'finished_at (UTC)':<20}rows")
        for job_name in STATUS_JOB_NAMES:
            row = db.execute(
                select(JobRun).where(JobRun.job_name == job_name).order_by(JobRun.started_at.desc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                print(f"{job_name:<26}{'never run':<10}")
                continue
            finished = row.finished_at.strftime("%Y-%m-%d %H:%M") if row.finished_at else "-"
            rows = row.rows_processed if row.rows_processed is not None else "-"
            print(f"{job_name:<26}{row.status:<10}{str(row.run_date):<12}{finished:<20}{rows}")
            if row.status == "failed" and row.error_message:
                print(f"  -> {row.error_message[:200]}")

        latest_price_date = db.execute(select(func.max(DailyPrice.trade_date))).scalar_one_or_none()
        today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
        expected = most_recent_trading_day(today_ist)
        if latest_price_date is None:
            freshness = "NO DATA"
        elif latest_price_date == expected:
            freshness = "current"
        else:
            freshness = f"STALE -- expected {expected}"
        print(f"\nlatest daily_prices date: {latest_price_date} ({freshness})")
    finally:
        db.close()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or inspect the daily ingest/screen pipeline")
    parser.add_argument("command", nargs="?", choices=["run", "status"], default="run")
    parser.add_argument("--dry-run", action="store_true", help="Report what each step would do, write nothing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.command == "status":
        print_status()
        return 0
    if args.dry_run:
        # A human inspecting what would happen -- no lock, no retries, single shot.
        try:
            run_pipeline(dry_run=True)
        except Exception as exc:
            logger.error("pipeline aborted: %s", exc)
            return 1
        return 0
    try:
        return 0 if run_pipeline_with_retries() else 1
    except OperationalError as exc:
        # Couldn't even acquire the advisory lock -- the DB was unreachable
        # before the pipeline did any real work, not a mid-run failure
        # run_pipeline's own retries are meant for. Alert immediately.
        logger.error("pipeline aborted -- database connection failed at startup: %s", exc)
        alerting.send_alert(
            "daily_pipeline: database connection failed at startup",
            f"Job: {PIPELINE_JOB_NAME}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}\n"
            f"Could not connect to the database when starting the pipeline.\n\n"
            f"Error: {exc}",
            fingerprint="daily_pipeline:db_connection_failed",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
