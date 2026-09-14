from datetime import datetime

from pydantic import BaseModel, Field


class HoldingCreate(BaseModel):
    instrument_id: int
    quantity: float = Field(gt=0)
    avg_cost: float = Field(gt=0)


class HoldingRow(BaseModel):
    id: int
    instrument_id: int
    symbol: str
    exchange: str
    company_name: str
    sector: str | None
    quantity: float
    avg_cost: float
    close: float | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    added_at: datetime


class SectorAllocationOut(BaseModel):
    sector: str
    market_value: float
    pct_of_portfolio: float


class PortfolioOut(BaseModel):
    total_market_value: float
    total_cost_basis: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float | None
    holdings: list[HoldingRow]
    allocation: list[SectorAllocationOut]
