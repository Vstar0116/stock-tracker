"""Portfolio CRUD + valuation. GET returns tiles/rows/allocation in one call;
POST/DELETE mutate a holding and return the same shape so the frontend never
needs a second round-trip to refresh.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_holding
from app.db.session import get_db
from app.models import Holding, Instrument, User
from app.schemas.portfolio import HoldingCreate, PortfolioOut
from app.services.portfolio import build_portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioOut)
def get_portfolio(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> PortfolioOut:
    return build_portfolio(db, current_user.id)


@router.post("", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
def upsert_holding(
    payload: HoldingCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PortfolioOut:
    if db.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    existing = db.execute(
        select(Holding).where(Holding.user_id == current_user.id, Holding.instrument_id == payload.instrument_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.quantity = payload.quantity
        existing.avg_cost = payload.avg_cost
    else:
        db.add(Holding(user_id=current_user.id, instrument_id=payload.instrument_id, quantity=payload.quantity, avg_cost=payload.avg_cost))
    db.commit()
    return build_portfolio(db, current_user.id)


@router.delete("/{holding_id}", response_model=PortfolioOut)
def delete_holding(
    holding: Holding = Depends(get_owned_holding), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PortfolioOut:
    db.delete(holding)
    db.commit()
    return build_portfolio(db, current_user.id)
