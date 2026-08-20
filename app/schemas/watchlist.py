from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.instrument import IndicatorOut

TrendState = Literal["uptrend", "downtrend", "neutral", "unknown"]


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WatchlistUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WatchlistOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WatchlistItemCreate(BaseModel):
    instrument_id: int
    notes: str | None = None


class WatchlistItemOut(BaseModel):
    instrument_id: int
    added_at: datetime
    notes: str | None

    model_config = {"from_attributes": True}


class WatchlistViewRow(BaseModel):
    """One row of GET /watchlists/{id}/view -- an instrument plus its latest
    price, latest indicators, and a trend_state derived from them."""

    instrument_id: int
    symbol: str
    exchange: str
    company_name: str
    sector: str | None
    trade_date: date | None
    close: float | None
    day_change_pct: float | None
    volume: int | None
    indicators: IndicatorOut | None
    trend_state: TrendState
    notes: str | None
    added_at: datetime
