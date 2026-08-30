from datetime import date, datetime
from typing import Literal

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
    error_message: str | None


class StatusDetailOut(StatusOut):
    last_successful_pipeline_run_at: datetime | None
    instrument_count: int
    daily_price_count: int
    indicator_count: int
    recent_job_runs: list[JobRunOut]
    nl_screen_configured: bool
    nl_screen_reachable: bool


class DownloadOut(BaseModel):
    """One NSE/BSE bhavcopy ingest attempt -- job_runs rows for job_name
    ingest_prices_nse/ingest_prices_bse, filtered to just those two and
    relabeled for the "download history" view (app/api/status.py)."""

    exchange: Literal["NSE", "BSE"]
    trade_date: date
    status: str
    rows_processed: int | None
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


class TriggerPipelineOut(BaseModel):
    triggered: bool
