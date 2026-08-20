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
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.jobs import compute_indicators, ingest_corporate_actions, ingest_instruments, ingest_prices, run_screens
from app.jobs._tracking import track_job_run
from app.jobs.ingest_prices import IST_OFFSET, most_recent_trading_day
from app.models import CorporateAction, DailyPrice, JobRun, Screen
from app.services.price_adjustment import apply_corporate_action

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_pipeline")

PIPELINE_JOB_NAME = "daily_pipeline"
APPLY_ACTIONS_JOB_NAME = "apply_corporate_actions"

INSTRUMENTS_REFRESH_DAYS = 7  # symbol master changes rarely -- no need to hit it every day


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
    try:
        run_pipeline(dry_run=args.dry_run)
    except Exception as exc:
        logger.error("pipeline aborted: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
