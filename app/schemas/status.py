from datetime import date, datetime

from pydantic import BaseModel


class StatusOut(BaseModel):
    latest_trade_date: date | None
    expected_trade_date: date
    is_current: bool
    last_pipeline_run_at: datetime | None
    last_pipeline_status: str | None
