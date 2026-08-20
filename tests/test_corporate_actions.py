"""Tests for corporate action ingestion + price adjustment -- the one place
that rewrites price history. See app/services/price_adjustment.py for the
ratio convention these tests are pinned to.

Run with: pytest tests/test_corporate_actions.py -v
Requires the local Postgres (docker compose up -d) -- each test runs inside a
SAVEPOINT-backed transaction that's always rolled back, so nothing here ever
touches the real dev data even though the code under test calls db.commit().
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs.ingest_corporate_actions import _parse_action_text
from app.models import CorporateAction, DailyPrice, Instrument
from app.services.price_adjustment import adjustment_factor, apply_corporate_action

EX_DATE = date(2026, 6, 15)
BEFORE_DATE = date(2026, 6, 10)
AFTER_DATE = date(2026, 6, 20)


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
def instrument(db):
    inst = Instrument(symbol="TESTCO", exchange="NSE", company_name="Test Co Ltd", is_active=True)
    db.add(inst)
    db.flush()
    return inst


def _add_price(db: Session, instrument: Instrument, trade_date: date, close: str) -> DailyPrice:
    close_dec = Decimal(close)
    price = DailyPrice(
        instrument_id=instrument.id,
        trade_date=trade_date,
        open=close_dec,
        high=close_dec,
        low=close_dec,
        close=close_dec,
        adjusted_close=close_dec,
        volume=1000,
    )
    db.add(price)
    db.flush()
    return price


class TestAdjustmentFactor:
    """Pure formula checks -- isolates the math from the DB update logic."""

    def test_split_1_for_5_factor_is_5(self):
        action = CorporateAction(action_type="SPLIT", ratio_from=1, ratio_to=5)
        assert adjustment_factor(action) == 5

    def test_bonus_2_for_1_factor_is_3(self):
        action = CorporateAction(action_type="BONUS", ratio_from=2, ratio_to=1)
        assert adjustment_factor(action) == 3

    def test_unknown_action_type_raises(self):
        action = CorporateAction(action_type="DIVIDEND", ratio_from=1, ratio_to=1)
        with pytest.raises(ValueError):
            adjustment_factor(action)


class TestApplyCorporateAction:
    def test_split_divides_pre_ex_date_adjusted_close_by_5(self, db, instrument):
        before = _add_price(db, instrument, BEFORE_DATE, "100.00")
        after = _add_price(db, instrument, AFTER_DATE, "20.00")
        action = CorporateAction(
            instrument_id=instrument.id,
            ex_date=EX_DATE,
            action_type="SPLIT",
            ratio_from=1,
            ratio_to=5,
            applied=False,
        )
        db.add(action)
        db.flush()

        rows_updated = apply_corporate_action(db, action)

        db.refresh(before)
        db.refresh(after)
        assert rows_updated == 1
        assert before.adjusted_close == Decimal("20.0000")  # 100 / 5
        assert after.adjusted_close == Decimal("20.00")  # on/after ex_date -- untouched
        assert action.applied is True

    def test_bonus_2_for_1_divides_pre_issue_adjusted_close_by_3(self, db, instrument):
        before = _add_price(db, instrument, BEFORE_DATE, "300.00")
        action = CorporateAction(
            instrument_id=instrument.id,
            ex_date=EX_DATE,
            action_type="BONUS",
            ratio_from=2,
            ratio_to=1,
            applied=False,
        )
        db.add(action)
        db.flush()

        apply_corporate_action(db, action)

        db.refresh(before)
        assert before.adjusted_close == Decimal("100.0000")  # 300 / 3

    def test_applying_twice_does_not_double_adjust(self, db, instrument):
        before = _add_price(db, instrument, BEFORE_DATE, "100.00")
        action = CorporateAction(
            instrument_id=instrument.id,
            ex_date=EX_DATE,
            action_type="SPLIT",
            ratio_from=1,
            ratio_to=5,
            applied=False,
        )
        db.add(action)
        db.flush()

        first_rows = apply_corporate_action(db, action)
        db.refresh(before)
        after_first = before.adjusted_close

        second_rows = apply_corporate_action(db, action)
        db.refresh(before)
        after_second = before.adjusted_close

        assert first_rows == 1
        assert second_rows == 0  # already claimed -- second call is a no-op
        assert after_first == after_second == Decimal("20.0000")  # NOT divided by 5 twice (would be 4.0000)

    def test_raw_close_column_is_never_modified(self, db, instrument):
        before = _add_price(db, instrument, BEFORE_DATE, "100.00")
        action = CorporateAction(
            instrument_id=instrument.id,
            ex_date=EX_DATE,
            action_type="SPLIT",
            ratio_from=1,
            ratio_to=5,
            applied=False,
        )
        db.add(action)
        db.flush()

        apply_corporate_action(db, action)

        db.refresh(before)
        assert before.close == Decimal("100.00")
        assert before.adjusted_close != before.close


class TestParseActionText:
    """Pins the free-text -> ratio_from/ratio_to translation against real
    captured NSE/BSE announcement text (fetched live 2026-08-18). The SPLIT
    cases check the from/to swap described in ingest_corporate_actions.py's
    module docstring -- get this backwards and every split silently inverts."""

    @pytest.mark.parametrize(
        "text,expected_type,expected_from,expected_to",
        [
            ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", "SPLIT", "1", "10"),
            ("Face Value Split (Sub-Division) - From Rs 5/- Per Share To Rs 2/- Per Share", "SPLIT", "2", "5"),
            ("Stock  Split From Rs.10/- to Rs.2/-", "SPLIT", "2", "10"),
            ("Bonus 2:1", "BONUS", "2", "1"),
            ("Bonus issue 1:10", "BONUS", "1", "10"),
        ],
    )
    def test_known_announcement_formats(self, text, expected_type, expected_from, expected_to):
        assert _parse_action_text(text) == (expected_type, expected_from, expected_to)

    def test_dividend_is_not_recognized(self):
        assert _parse_action_text("Interim Dividend - Rs 5 Per Share") is None
