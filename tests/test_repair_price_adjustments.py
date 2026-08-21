"""Tests for the one-off repair of daily_prices rows the pre-gate
step_apply_corporate_actions race left unadjusted (see
app/jobs/repair_price_adjustments.py's docstring for the full story).

Tests call repair_instrument() directly, scoped to one instrument_id, rather
than the module's run() (which scans every instrument with an applied
action) -- run() against this shared dev database also processes whatever
real corporate actions already exist there, which is correct behavior but
makes per-test row-count assertions meaningless and each call slow (a full
indicator recompute per real affected instrument). run()'s own logic is a
thin loop over repair_instrument() plus a dry_run flag it already threads
through untouched, so unit-testing repair_instrument() covers the real
logic; run() itself was exercised for real against the actual dev database
(see the session's investigation) as the stronger end-to-end proof.

Run with: pytest tests/test_repair_price_adjustments.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs import repair_price_adjustments
from app.models import CorporateAction, DailyPrice, Indicator, Instrument

EX_DATE = date(2026, 6, 15)


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
    inst = Instrument(symbol="REPAIRCO", exchange="NSE", company_name="Repair Co Ltd", is_active=True)
    db.add(inst)
    db.flush()
    return inst


def _price(db, instrument, trade_date, close, adjusted_close=None):
    close_dec = Decimal(close)
    row = DailyPrice(
        instrument_id=instrument.id, trade_date=trade_date, open=close_dec, high=close_dec, low=close_dec,
        close=close_dec, adjusted_close=Decimal(adjusted_close) if adjusted_close is not None else close_dec,
        volume=1000,
    )
    db.add(row)
    db.flush()
    return row


class TestRepairInstrument:
    def test_fixes_a_row_missed_by_the_race_without_touching_the_correctly_adjusted_one(self, db, instrument):
        # Simulates exactly the observed bug: an older row that WAS adjusted
        # when the action was first (prematurely) applied, and a newer row
        # -- still before ex_date -- that arrived afterward and was missed.
        correctly_adjusted = _price(db, instrument, date(2026, 1, 5), "500.00", adjusted_close="100.0000")
        straggler = _price(db, instrument, date(2026, 6, 10), "480.00")  # never touched -- close == adjusted_close
        after_ex_date = _price(db, instrument, date(2026, 6, 20), "96.00")  # post-split, untouched (correct)
        action = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=True,
        )
        db.add(action)
        db.flush()

        fixed = repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=False)

        assert fixed == 1  # only the straggler
        db.refresh(correctly_adjusted)
        db.refresh(straggler)
        db.refresh(after_ex_date)
        assert correctly_adjusted.adjusted_close == Decimal("100.0000")  # unchanged, not double-divided
        assert straggler.adjusted_close == Decimal("96.0000")  # 480 / 5, now fixed
        assert after_ex_date.adjusted_close == Decimal("96.00")  # on/after ex_date -- never touched

    def test_dry_run_reports_but_writes_nothing(self, db, instrument):
        straggler = _price(db, instrument, date(2026, 6, 10), "480.00")
        action = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=True,
        )
        db.add(action)
        db.flush()

        fixed = repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=True)

        assert fixed == 1
        db.refresh(straggler)
        assert straggler.adjusted_close == Decimal("480.00")  # untouched

    def test_rerunning_after_a_fix_is_a_no_op(self, db, instrument):
        _price(db, instrument, date(2026, 6, 10), "480.00")
        action = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=True,
        )
        db.add(action)
        db.flush()

        first = repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=False)
        second = repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=False)

        assert first == 1
        assert second == 0

    def test_stacked_split_and_bonus_on_same_ex_date_compose_multiplicatively(self, db, instrument):
        # Several real instruments in production data have exactly this: two
        # actions, same ex_date. A straggler must be divided by BOTH factors,
        # not just whichever action's repair pass happens to run "first".
        straggler = _price(db, instrument, date(2026, 6, 10), "900.00")
        split = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=True,
        )
        bonus = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="BONUS",
            ratio_from=2, ratio_to=1, applied=True,
        )
        db.add_all([split, bonus])
        db.flush()

        repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=False)

        db.refresh(straggler)
        assert straggler.adjusted_close == Decimal("60.0000")  # 900 / (5 * 3)


class TestRecomputeIndicators:
    def test_recomputes_indicators_after_a_repair(self, db, instrument):
        for i in range(25):
            _price(db, instrument, date(2026, 1, 1 + i), "100.00", adjusted_close="100.0000")
        _price(db, instrument, date(2026, 6, 10), "480.00")  # straggler, in the recomputed window
        action = CorporateAction(
            instrument_id=instrument.id, ex_date=EX_DATE, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=True,
        )
        db.add(action)
        db.flush()

        repair_price_adjustments.repair_instrument(db, instrument.id, dry_run=False)
        rows = repair_price_adjustments._recompute_indicators(db, instrument.id)

        assert rows > 0
        latest = (
            db.query(Indicator)
            .filter(Indicator.instrument_id == instrument.id)
            .order_by(Indicator.trade_date.desc())
            .first()
        )
        assert latest is not None
