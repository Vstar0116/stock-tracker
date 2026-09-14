from datetime import date

from pydantic import BaseModel

from app.schemas.public import MarketMoverOut


class DashboardMoverOut(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    sector: str | None
    close: float
    change_pct: float


class FiredScreenOut(BaseModel):
    screen_id: int
    name: str
    count: int


class SectorHeatOut(BaseModel):
    sector: str
    change_pct: float
    count: int


class DashboardOut(BaseModel):
    as_of: date
    watchlist_count: int
    instrument_count: int
    up_count: int
    down_count: int
    moved_2pct_count: int
    golden_cross_count: int
    volume_breakout_count: int
    alerts_today: int
    ticker: list[MarketMoverOut]
    movers: list[DashboardMoverOut]
    fired_screens: list[FiredScreenOut]
    heatmap: list[SectorHeatOut]
