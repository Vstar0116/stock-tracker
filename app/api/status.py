import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, pagination
from app.db.session import get_db
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.jobs.ingest_prices import IST_OFFSET, most_recent_trading_day
from app.models import DailyPrice, Indicator, Instrument, JobRun, User
from app.rate_limit import RateLimiter
from app.schemas.common import Page
from app.schemas.status import DownloadOut, JobRunOut, StatusDetailOut, StatusOut, TriggerPipelineOut
from app.services.nl_screen import nl_screen_status

router = APIRouter(prefix="/api/status", tags=["status"], dependencies=[Depends(get_current_user)])

DOWNLOAD_JOB_NAMES = ["ingest_prices_nse", "ingest_prices_bse"]

# repo root: app/api/status.py -> app/api -> app -> repo root. daily_pipeline.py
# is invoked the same way the systemd unit runs it (python -m app.jobs.daily_pipeline),
# just triggered on demand instead of on a timer.
REPO_ROOT = Path(__file__).resolve().parents[2]

# A full pipeline run hits live NSE/BSE endpoints and takes real minutes --
# 3/day is generous for on-demand catch-up or local testing while keeping a
# 5-user deployment from hammering the exchanges or piling up overlapping
# runs (the pipeline's own advisory lock makes an overlap a no-op, not a
# crash, but there's still no reason to invite it).
pipeline_trigger_limiter = RateLimiter(
    key_prefix="pipeline_trigger:user",
    max_requests=3,
    window_seconds=86400,
    message="daily limit for manual pipeline runs reached (3/day) -- the scheduled 20:30 IST run covers the rest",
)


def _freshness(db: Session) -> tuple:
    latest_trade_date = db.execute(select(func.max(DailyPrice.trade_date))).scalar_one_or_none()
    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
    expected = most_recent_trading_day(today_ist)
    return latest_trade_date, expected


@router.get("", response_model=StatusOut)
def get_status(db: Session = Depends(get_db)) -> StatusOut:
    """Thin, polled by the nav bar every 5 minutes -- keep this cheap. See
    /api/status/detail for the full admin view (row counts, job history,
    NL-screening reachability), loaded on demand only."""
    latest_trade_date, expected = _freshness(db)

    last_run = db.execute(
        select(JobRun).where(JobRun.job_name == PIPELINE_JOB_NAME).order_by(JobRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()

    return StatusOut(
        latest_trade_date=latest_trade_date,
        expected_trade_date=expected,
        is_current=latest_trade_date == expected,
        last_pipeline_run_at=last_run.finished_at if last_run else None,
        last_pipeline_status=last_run.status if last_run else None,
    )


@router.get("/detail", response_model=StatusDetailOut)
def get_status_detail(db: Session = Depends(get_db)) -> StatusDetailOut:
    latest_trade_date, expected = _freshness(db)

    last_run = db.execute(
        select(JobRun).where(JobRun.job_name == PIPELINE_JOB_NAME).order_by(JobRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    last_success = db.execute(
        select(JobRun)
        .where(JobRun.job_name == PIPELINE_JOB_NAME, JobRun.status == "success")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    instrument_count = db.execute(select(func.count()).select_from(Instrument)).scalar_one()
    daily_price_count = db.execute(select(func.count()).select_from(DailyPrice)).scalar_one()
    indicator_count = db.execute(select(func.count()).select_from(Indicator)).scalar_one()

    recent_runs = db.execute(select(JobRun).order_by(JobRun.started_at.desc()).limit(10)).scalars().all()
    nl_configured, nl_reachable = nl_screen_status()

    return StatusDetailOut(
        latest_trade_date=latest_trade_date,
        expected_trade_date=expected,
        is_current=latest_trade_date == expected,
        last_pipeline_run_at=last_run.finished_at if last_run else None,
        last_pipeline_status=last_run.status if last_run else None,
        last_successful_pipeline_run_at=last_success.finished_at if last_success else None,
        instrument_count=instrument_count,
        daily_price_count=daily_price_count,
        indicator_count=indicator_count,
        recent_job_runs=[
            JobRunOut(
                job_name=r.job_name,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_seconds=(r.finished_at - r.started_at).total_seconds() if r.finished_at else None,
                rows_processed=r.rows_processed,
            )
            for r in recent_runs
        ],
        nl_screen_configured=nl_configured,
        nl_screen_reachable=nl_reachable,
    )


@router.get("/downloads", response_model=Page[DownloadOut])
def list_downloads(db: Session = Depends(get_db), page: Pagination = Depends(pagination)) -> Page[DownloadOut]:
    """NSE/BSE bhavcopy ingest history -- the job_runs rows ingest_prices.py's
    run_for_exchange() already writes on every attempt, filtered down to just
    those two job names and relabeled as "downloads" (each row is one
    exchange's bhavcopy fetch+parse+load for one trade date)."""
    total = db.execute(
        select(func.count()).select_from(JobRun).where(JobRun.job_name.in_(DOWNLOAD_JOB_NAMES))
    ).scalar_one()
    rows = (
        db.execute(
            select(JobRun)
            .where(JobRun.job_name.in_(DOWNLOAD_JOB_NAMES))
            .order_by(JobRun.started_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    items = [
        DownloadOut(
            exchange="NSE" if r.job_name == "ingest_prices_nse" else "BSE",
            trade_date=r.run_date,
            status=r.status,
            rows_processed=r.rows_processed,
            started_at=r.started_at,
            finished_at=r.finished_at,
            error_message=r.error_message,
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/run-now", response_model=TriggerPipelineOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_pipeline(current_user: User = Depends(get_current_user)) -> TriggerPipelineOut:
    """Fire-and-forget on-demand run of the same entrypoint the systemd timer
    uses. Doesn't block on the pipeline finishing (it can take minutes) --
    check GET /api/status or /api/status/downloads afterward for the outcome.
    Safe even if the scheduled run is already in flight: run_pipeline_with_retries's
    non-blocking advisory lock (app/jobs/daily_pipeline.py) makes a second
    concurrent invocation exit immediately without touching data."""
    pipeline_trigger_limiter.check(str(current_user.id))
    subprocess.Popen(
        [sys.executable, "-m", "app.jobs.daily_pipeline"],
        cwd=REPO_ROOT,
        start_new_session=True,
    )
    return TriggerPipelineOut(triggered=True)
