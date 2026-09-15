"""Response shapes for app/api/public.py -- the one router with no
get_current_user dependency. Every field here is either whole-market data
that NSE already publishes (symbol/exchange/close/% change) or a bare
aggregate count (instrument_count, alerts_today) with no user or screen
identity attached -- nothing here can leak which account a screen or
watchlist belongs to."""

from datetime import date

from pydantic import BaseModel


class MarketMoverOut(BaseModel):
    symbol: str
    exchange: str
    close: float
    change_pct: float


class MarketSnapshotOut(BaseModel):
    as_of: date
    instrument_count: int
    up_count: int
    down_count: int
    moved_2pct_count: int
    golden_cross_count: int
    volume_breakout_count: int
    alerts_today: int
    ticker: list[MarketMoverOut]
    top_movers: list[MarketMoverOut]
    golden_cross: list[MarketMoverOut]
    volume_breakout: list[MarketMoverOut]
