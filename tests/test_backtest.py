"""Tests for app/services/backtest.py.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, same pattern as
tests/test_portfolio.py.

The trading calendar backtest.run_backtest() scans is global (every
instrument's daily_prices rows, per screening.py's shared-calendar
assumption), so these tests anchor synthetic rows to the DB's own real
recent trade_dates rather than inventing a date range -- that keeps the
math exact without needing an empty database.

Run with: pytest tests/test_backtest.py -v
"""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Instrument
from app.schemas.screen import In
from app.services import backtest as backtest_service

TEST_SECTOR = "ZZ_BACKTEST_TEST_SECTOR"


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


def _seed_rising_instrument(db: Session, symbol: str, sector: str, dates: list[date]) -> int:
    """One instrument, tagged with a sector no real data uses, whose
    adjusted_close rises by 1 on each of the given (real) trade_dates --
    every forward return within that date range is positive by
    construction, so hit rate and sample size are exact and easy to assert."""
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True, sector=sector)
    db.add(inst)
    db.flush()
    for i, d in enumerate(dates):
        close = 100.0 + i
        db.add(
            DailyPrice(
                instrument_id=inst.id, trade_date=d,
                open=close, high=close, low=close, close=close, adjusted_close=close, volume=100_000,
            )
        )
    db.flush()
    return inst.id


class TestRunBacktest:
    def test_forward_returns_on_monotonically_rising_prices(self, db):
        all_dates = db.execute(select(DailyPrice.trade_date).distinct().order_by(DailyPrice.trade_date)).scalars().all()
        if len(all_dates) < 120:
            pytest.skip("not enough historical price data loaded in this DB for a meaningful backtest window")

        recent = all_dates[-100:]
        _seed_rising_instrument(db, "RISEZZ", TEST_SECTOR, recent)
        rule = In(field="sector", values=[TEST_SECTOR])

        result = backtest_service.run_backtest(db, rule, lookback_days=250)

        # Only dates with a full forward horizon still ahead of them are
        # testable at all -- the tail max(HORIZONS) dates of our seeded
        # range never get evaluated as an as_of_date.
        expected_matches = len(recent) - max(backtest_service.HORIZONS)
        assert result.total_matches == expected_matches

        five_day = next(h for h in result.horizons if h.horizon_days == 5)
        summary = backtest_service.summarize(five_day)
        assert summary["sample_size"] == expected_matches
        assert summary["hit_rate_pct"] == 100.0
        assert summary["avg_return_pct"] > 0
        assert summary["worst_return_pct"] > 0

    def test_no_matches_when_rule_matches_nothing(self, db):
        rule = In(field="sector", values=["ZZ_SECTOR_THAT_DOES_NOT_EXIST_ANYWHERE"])

        result = backtest_service.run_backtest(db, rule, lookback_days=50)

        assert result.total_matches == 0
        assert all(backtest_service.summarize(h)["sample_size"] == 0 for h in result.horizons)
