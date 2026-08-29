"""Request/response shapes for the custom crossover indicator and scan
(app/api/crossover.py). Mirrors app/schemas/screen.py's pattern: validation
lives on the schema, reusing the same rules the compute layer enforces."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.crossover import validate_periods

MaType = Literal["sma", "ema"]
Signal = Literal["crossed_above", "crossed_below"]
Direction = Literal["crossed_above", "crossed_below", "any"]


class CrossoverPoint(BaseModel):
    trade_date: date
    fast: float | None
    slow: float | None
    signal: Signal | None


class CrossoverSeriesOut(BaseModel):
    instrument_id: int
    fast: int
    slow: int
    ma_type: MaType
    points: list[CrossoverPoint]


class ScanRequest(BaseModel):
    fast: int = Field(ge=1)
    slow: int = Field(ge=1)
    ma_type: MaType
    direction: Direction = "any"
    # Restrict the scan to one uploaded portfolio-report's matched tickers
    # (app/api/portfolio_reports.py), optionally intersected with the
    # current user's saved watchlists. Both None/False -> unchanged,
    # whole-market behavior.
    report_id: int | None = None
    watchlist_only: bool = False

    @model_validator(mode="after")
    def _valid_periods(self) -> "ScanRequest":
        validate_periods(self.fast, self.slow)
        return self


class ScanStats(BaseModel):
    evaluated: int
    matched: int
    skipped_insufficient_history: int
    skipped_stale: int
    elapsed_ms: int
    cached: bool
    # Size of the report/watchlist-restricted universe this scan actually
    # ran against, when report_id or watchlist_only narrowed it -- None
    # under the default whole-market scan, where `evaluated` already means
    # exactly that.
    universe: int | None = None


class ScanMatchOut(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    sector: str | None
    latest_close: float | None
    signal: Signal
    # Present only when the scan was scoped to a report (report_id set) --
    # that report's own recorded values for this ticker, hydrated from
    # portfolio_report_items alongside the instrument row below.
    pdf_group: str | None = None
    pdf_score: int | None = None
    pdf_price: float | None = None
    pdf_zone: str | None = None


class ScanResponse(BaseModel):
    as_of: date
    params: ScanRequest
    stats: ScanStats
    matches: list[ScanMatchOut]
