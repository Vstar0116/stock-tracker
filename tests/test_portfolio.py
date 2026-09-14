"""Tests for app/services/portfolio.py.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, same pattern as
tests/test_dashboard_snapshot.py.

Run with: pytest tests/test_portfolio.py -v
"""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Holding, Instrument, User
from app.services import portfolio as portfolio_service


@pytest.fixture()
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _seed_user(db: Session, email: str) -> int:
    user = User(email=email, name="Portfolio Test", password_hash="x")
    db.add(user)
    db.flush()
    return user.id


def _seed_instrument(db: Session, symbol: str, close: float, sector: str | None = None) -> int:
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True, sector=sector)
    db.add(inst)
    db.flush()
    db.add(DailyPrice(instrument_id=inst.id, trade_date=date(2026, 1, 2), open=close, high=close, low=close, close=close, adjusted_close=close, volume=100_000))
    db.flush()
    return inst.id


class TestBuildPortfolio:
    def test_market_value_and_unrealized_pnl(self, db):
        user_id = _seed_user(db, "pf-value@example.com")
        inst_id = _seed_instrument(db, "PFCO", close=150.0)
        db.add(Holding(user_id=user_id, instrument_id=inst_id, quantity=10, avg_cost=100.0))
        db.flush()

        out = portfolio_service.build_portfolio(db, user_id)

        assert out.total_market_value == pytest.approx(1500.0)
        assert out.total_cost_basis == pytest.approx(1000.0)
        assert out.total_unrealized_pnl == pytest.approx(500.0)
        assert out.total_unrealized_pnl_pct == pytest.approx(50.0)
        assert out.holdings[0].symbol == "PFCO"

    def test_only_the_requesting_users_holdings_are_included(self, db):
        user_id = _seed_user(db, "pf-scope@example.com")
        other_id = _seed_user(db, "pf-scope-other@example.com")
        inst_id = _seed_instrument(db, "MINEONLY", close=100.0)
        db.add(Holding(user_id=user_id, instrument_id=inst_id, quantity=5, avg_cost=90.0))
        db.add(Holding(user_id=other_id, instrument_id=inst_id, quantity=99, avg_cost=1.0))
        db.flush()

        out = portfolio_service.build_portfolio(db, user_id)

        assert len(out.holdings) == 1
        assert out.holdings[0].quantity == 5

    def test_sector_allocation_groups_and_sums_to_total(self, db):
        user_id = _seed_user(db, "pf-alloc@example.com")
        tech_a = _seed_instrument(db, "TECHA", close=100.0, sector="Technology")
        tech_b = _seed_instrument(db, "TECHB", close=100.0, sector="Technology")
        bank = _seed_instrument(db, "BANKCO", close=100.0, sector="Banking")
        db.add(Holding(user_id=user_id, instrument_id=tech_a, quantity=10, avg_cost=50.0))
        db.add(Holding(user_id=user_id, instrument_id=tech_b, quantity=10, avg_cost=50.0))
        db.add(Holding(user_id=user_id, instrument_id=bank, quantity=10, avg_cost=50.0))
        db.flush()

        out = portfolio_service.build_portfolio(db, user_id)

        tech = next(a for a in out.allocation if a.sector == "Technology")
        bank_alloc = next(a for a in out.allocation if a.sector == "Banking")
        assert tech.market_value == pytest.approx(2000.0)
        assert bank_alloc.market_value == pytest.approx(1000.0)
        assert tech.pct_of_portfolio == pytest.approx(200 / 3)

    def test_holding_with_no_price_history_has_null_valuation(self, db):
        user_id = _seed_user(db, "pf-nodata@example.com")
        inst = Instrument(symbol="NODATA", exchange="NSE", company_name="No Data", is_active=True)
        db.add(inst)
        db.flush()
        db.add(Holding(user_id=user_id, instrument_id=inst.id, quantity=3, avg_cost=20.0))
        db.flush()

        out = portfolio_service.build_portfolio(db, user_id)

        assert out.holdings[0].close is None
        assert out.holdings[0].market_value is None
        assert out.total_market_value == pytest.approx(0.0)
