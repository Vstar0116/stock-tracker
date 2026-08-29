"""Request/response shapes for POST /api/portfolio-reports and its scan-time
use from app/schemas/crossover.py. Mirrors app/schemas/watchlist.py's
from_attributes pattern for the read models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Zone = Literal["A", "B", "C", "D"]


class PortfolioReportItemOut(BaseModel):
    ticker: str
    instrument_id: int | None
    matched: bool
    symbol: str | None
    grp: str | None
    score: int | None
    pdf_price: float | None
    zone: Zone | None

    model_config = {"from_attributes": True}


class PortfolioReportOut(BaseModel):
    id: int
    filename: str
    report_date: date | None
    uploaded_at: datetime
    ticker_count: int
    matched_count: int
    items: list[PortfolioReportItemOut]

    model_config = {"from_attributes": True}


class PortfolioReportSummary(BaseModel):
    """Lighter shape for the past-uploads list -- no per-ticker rows."""

    id: int
    filename: str
    report_date: date | None
    uploaded_at: datetime
    ticker_count: int
    matched_count: int

    model_config = {"from_attributes": True}
