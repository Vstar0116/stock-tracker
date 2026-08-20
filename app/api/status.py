from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.jobs.ingest_prices import IST_OFFSET, most_recent_trading_day
from app.models import DailyPrice, Indicator, Instrument, JobRun
from app.schemas.status import JobRunOut, StatusDetailOut, StatusOut
from app.services.nl_screen import nl_screen_status

router = APIRouter(prefix="/api/status", tags=["status"], dependencies=[Depends(get_current_user)])


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
