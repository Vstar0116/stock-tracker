from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class HoldingCreate(BaseModel):
    instrument_id: int
    quantity: Decimal = Field(gt=0)
    avg_cost: Decimal = Field(gt=0)


class HoldingRow(BaseModel):
    id: int
    instrument_id: int
    symbol: str
    exchange: str
    company_name: str
    sector: str | None
    quantity: Decimal
    avg_cost: Decimal
    close: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: float | None
    added_at: datetime


class SectorAllocationOut(BaseModel):
    sector: str
    market_value: Decimal
    pct_of_portfolio: float


class PortfolioOut(BaseModel):
    total_market_value: Decimal
    total_cost_basis: Decimal
    total_unrealized_pnl: Decimal
    total_unrealized_pnl_pct: float | None
    holdings: list[HoldingRow]
    allocation: list[SectorAllocationOut]
