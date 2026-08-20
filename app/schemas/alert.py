from datetime import date, datetime

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: int
    screen_id: int
    screen_name: str
    instrument_id: int
    symbol: str
    exchange: str
    trade_date: date
    triggered_at: datetime
    snapshot: dict
    seen: bool
