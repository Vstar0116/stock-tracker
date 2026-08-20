from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.jobs.ingest_prices import IST_OFFSET, most_recent_trading_day
from app.models import DailyPrice, JobRun
from app.schemas.status import StatusOut

router = APIRouter(prefix="/api/status", tags=["status"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=StatusOut)
def get_status(db: Session = Depends(get_db)) -> StatusOut:
    latest_trade_date = db.execute(select(func.max(DailyPrice.trade_date))).scalar_one_or_none()
    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
    expected = most_recent_trading_day(today_ist)

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
