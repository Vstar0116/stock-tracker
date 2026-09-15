from datetime import date

from pydantic import BaseModel, Field

from app.schemas.screen import ScreenRule


class BacktestRequest(BaseModel):
    definition: ScreenRule
    lookback_days: int = Field(default=250, ge=20, le=500)


class HorizonStats(BaseModel):
    horizon_days: int
    sample_size: int
    hit_rate_pct: float | None
    avg_return_pct: float | None
    median_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None


class BacktestResponse(BaseModel):
    as_of: date | None
    dates_evaluated: int
    total_matches: int
    horizons: list[HorizonStats]
