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


class ScanMatchOut(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    sector: str | None
    latest_close: float | None
    signal: Signal


class ScanResponse(BaseModel):
    as_of: date
    params: ScanRequest
    stats: ScanStats
    matches: list[ScanMatchOut]
