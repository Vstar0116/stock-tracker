from typing import Literal

from pydantic import BaseModel


class ZoneOut(BaseModel):
    instrument_id: int
    ticker: str
    zone: Literal["A", "B", "C", "D", "Unclassified", "Insufficient Data"]
    zone_label: str
    rsi: float | None
    price: float | None
    macro_sma: float | None
    fast_ema: float | None
    slow_ema: float | None
    atr_band_lower: float | None
    atr_band_upper: float | None
    rvol: float | None
    reason: str


class SkippedOut(BaseModel):
    instrument_id: int
    ticker: str
    reason: str


class ZoneParamsOut(BaseModel):
    macro_sma_period: int
    fast_ema_period: int
    slow_ema_period: int
    rsi_period: int
    rsi_zone_a_max: float
    rsi_zone_b_range: tuple[float, float]
    rsi_zone_c_range: tuple[float, float]
    rsi_zone_d_min: float
    atr_period: int
    atr_limit_multiplier: float
    rvol_period: int
    near_ema_pct: float


class ZoneScanResponse(BaseModel):
    as_of: str
    params: ZoneParamsOut
    matches: list[ZoneOut]
    skipped: list[SkippedOut]
    evaluated: int
    cached: bool
    elapsed_ms: int
