"""Portfolio valuation: joins the user's holdings against each instrument's
latest close (same correlated-LATERAL shape as view_watchlist in
app/api/watchlists.py) and aggregates market value / unrealized P&L, plus a
sector allocation breakdown.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.models import DailyPrice, Holding, Instrument
from app.schemas.portfolio import HoldingRow, PortfolioOut, SectorAllocationOut

UNSECTORED = "Uncategorized"


def build_portfolio(db: Session, user_id: int) -> PortfolioOut:
    latest_price = (
        select(DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == Instrument.id)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
        .lateral("pf_price")
    )
    stmt = (
        select(
            Holding.id,
            Holding.instrument_id,
            Holding.quantity,
            Holding.avg_cost,
            Holding.created_at,
            Instrument.symbol,
            Instrument.exchange,
            Instrument.company_name,
            Instrument.sector,
            latest_price.c.adjusted_close.label("close"),
        )
        .select_from(Holding)
        .join(Instrument, Instrument.id == Holding.instrument_id)
        .outerjoin(latest_price, true())
        .where(Holding.user_id == user_id)
        .order_by(Instrument.symbol)
    )

    holdings: list[HoldingRow] = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    value_by_sector: dict[str, float] = defaultdict(float)

    for row in db.execute(stmt).all():
        quantity, avg_cost = float(row.quantity), float(row.avg_cost)
        close = float(row.close) if row.close is not None else None
        market_value = close * quantity if close is not None else None
        cost_basis = avg_cost * quantity
        unrealized_pnl = market_value - cost_basis if market_value is not None else None
        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if unrealized_pnl is not None and cost_basis else None

        total_cost_basis += cost_basis
        if market_value is not None:
            total_market_value += market_value
            value_by_sector[row.sector or UNSECTORED] += market_value

        holdings.append(
            HoldingRow(
                id=row.id,
                instrument_id=row.instrument_id,
                symbol=row.symbol,
                exchange=row.exchange,
                company_name=row.company_name,
                sector=row.sector,
                quantity=quantity,
                avg_cost=avg_cost,
                close=close,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                added_at=row.created_at,
            )
        )

    total_unrealized_pnl = total_market_value - total_cost_basis
    total_unrealized_pnl_pct = (total_unrealized_pnl / total_cost_basis * 100) if total_cost_basis else None
    allocation = sorted(
        (
            SectorAllocationOut(
                sector=sector,
                market_value=value,
                pct_of_portfolio=(value / total_market_value * 100) if total_market_value else 0.0,
            )
            for sector, value in value_by_sector.items()
        ),
        key=lambda a: a.market_value,
        reverse=True,
    )

    return PortfolioOut(
        total_market_value=total_market_value,
        total_cost_basis=total_cost_basis,
        total_unrealized_pnl=total_unrealized_pnl,
        total_unrealized_pnl_pct=total_unrealized_pnl_pct,
        holdings=holdings,
        allocation=allocation,
    )
