"""Tests for the DB-computed fundamentals ratio columns (app/models/fundamentals.py).

Pins the actual Postgres-evaluated formulas, not a Python re-implementation --
catches a typo in the generated-column SQL (e.g. the cash_conversion_cycle
formula, which has to repeat its sub-formulas inline since Postgres generated
columns can't reference other generated columns) that a pure-Python test of
the "same" math would never see.

Run with: pytest tests/test_fundamentals.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import Fundamental, Instrument

AS_OF = date(2026, 3, 31)


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


@pytest.fixture()
def fund(db):
    inst = Instrument(symbol="FUNDTEST", exchange="NSE", company_name="Fund Test Co", is_active=True)
    db.add(inst)
    db.flush()
    f = Fundamental(
        instrument_id=inst.id,
        as_of_date=AS_OF,
        net_profit=Decimal("200"),
        equity=Decimal("1000"),
        receivables=Decimal("300"),
        payables=Decimal("150"),
        inventory=Decimal("100"),
        sales=Decimal("3650"),  # deliberately 3650 = 365*10 so /sales*365 gives clean round numbers
        cogs=Decimal("1825"),  # 365*5
    )
    db.add(f)
    db.flush()
    db.refresh(f)
    return f


class TestQualityRatios:
    def test_roe(self, fund):
        assert fund.roe == Decimal("20.0000")  # 200/1000*100

    def test_debtor_days(self, fund):
        assert fund.debtor_days == Decimal("30.0000")  # 300/3650*365 = 30

    def test_payable_days(self, fund):
        assert fund.payable_days == Decimal("30.0000")  # 150/1825*365 = 30

    def test_inventory_days(self, fund):
        assert fund.inventory_days == Decimal("20.0000")  # 100/1825*365 = 20

    def test_cash_conversion_cycle(self, fund):
        # inventory_days + debtor_days - payable_days = 20 + 30 - 30 = 20
        assert fund.cash_conversion_cycle == Decimal("20.0000")

    def test_working_capital_days(self, fund):
        # (300 + 100 - 150) / 3650 * 365 = 25
        assert fund.working_capital_days == Decimal("25.0000")

    def test_all_null_when_denominators_are_zero_or_unset(self, db):
        inst = Instrument(symbol="FUNDNULLTEST", exchange="NSE", company_name="Fund Null Co", is_active=True)
        db.add(inst)
        db.flush()
        f = Fundamental(instrument_id=inst.id, as_of_date=AS_OF, net_profit=Decimal("100"))  # equity/sales/cogs unset
        db.add(f)
        db.flush()
        db.refresh(f)
        assert f.roe is None
        assert f.debtor_days is None
        assert f.cash_conversion_cycle is None
        assert f.working_capital_days is None
