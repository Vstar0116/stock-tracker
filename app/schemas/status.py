from datetime import date, datetime

from pydantic import BaseModel


class StatusOut(BaseModel):
    latest_trade_date: date | None
    expected_trade_date: date
    is_current: bool
    last_pipeline_run_at: datetime | None
    last_pipeline_status: str | None


class JobRunOut(BaseModel):
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    rows_processed: int | None


class StatusDetailOut(StatusOut):
    last_successful_pipeline_run_at: datetime | None
    instrument_count: int
    daily_price_count: int
    indicator_count: int
    recent_job_runs: list[JobRunOut]
    nl_screen_configured: bool
    nl_screen_reachable: bool
