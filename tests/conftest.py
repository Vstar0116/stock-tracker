"""Session-wide safety net for the market-wide scan tests (crossover_loader,
zone_loader, and the API tests that hit their endpoints). Those tests are
documented to assume the DB already carries real, backfilled market data --
true for the local dev DB, never true for CI's fresh Postgres service
(migrations only, no data). Without a real trade-date calendar to anchor
seeded rows to, `_recent_trade_dates` returns nothing and market-wide
queries raise "no price data loaded yet".

Only seeds when daily_prices is empty, so it's a no-op against the real dev
DB and never touches its data.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Instrument

BASELINE_TRADE_DAYS = 30


@pytest.fixture(scope="session", autouse=True)
def ensure_baseline_market_data():
    with Session(engine) as session:
        if session.execute(text("SELECT 1 FROM daily_prices LIMIT 1")).first() is not None:
            return

        inst = Instrument(symbol="CIBASELINE", exchange="NSE", company_name="CI Baseline Co", is_active=True)
        session.add(inst)
        session.flush()

        start = date(2026, 1, 1)
        for i in range(BASELINE_TRADE_DAYS):
            session.add(
                DailyPrice(
                    instrument_id=inst.id,
                    trade_date=start + timedelta(days=i),
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    adjusted_close=Decimal("100"),
                    volume=100000,
                )
            )
        session.commit()
