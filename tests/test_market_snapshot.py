"""Tests for app/services/market_snapshot.py -- the public landing page's
only source of real numbers.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back. Same pattern as
tests/test_crossover_loader.py: market_snapshot opens its own connection, so
tests point that at the SAVEPOINT connection instead.

Run with: pytest tests/test_market_snapshot.py -v
"""

import contextlib
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Indicator, Instrument
from app.services import market_snapshot


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


def _recent_trade_dates(db: Session, n: int) -> list[date]:
    rows = db.execute(
        text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"),
        {"n": n},
    ).fetchall()
    return sorted(r[0] for r in rows)


def _seed(db: Session, symbol: str, closes: list[float], dates: list[date], volumes: list[int] | None = None) -> int:
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True)
    db.add(inst)
    db.flush()
    volumes = volumes or [100_000] * len(closes)
    for d, close, vol in zip(dates, closes, volumes):
        db.add(
            DailyPrice(
                instrument_id=inst.id, trade_date=d, open=close, high=close, low=close,
                close=close, adjusted_close=close, volume=vol,
            )
        )
    db.flush()
    return inst.id


def _seed_indicator(db: Session, instrument_id: int, trade_date: date, sma_200: float | None = None, volume_sma_20: float | None = None) -> None:
    db.add(Indicator(instrument_id=instrument_id, trade_date=trade_date, sma_200=sma_200, volume_sma_20=volume_sma_20))
    db.flush()


class TestBuildSnapshot:
    """The real dev DB carries ~7,500 active instruments' real market data
    (see tests/test_crossover_loader.py's module docstring for the same
    situation) -- a modest seeded move can rank outside the top-N lists on
    any given day. Assert full-market aggregate deltas (before vs. after
    seeding), never presence in a top-N list, same convention as
    test_crossover_loader.py.
    """

    def test_movers_pct_and_up_down_classification(self, db, monkeypatch):
        monkeypatch.setattr(market_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
        market_snapshot._build_cached.cache_clear()
        before = market_snapshot.build_snapshot(db)

        dates = _recent_trade_dates(db, 2)
        _seed(db, "UPCO", [100.0, 105.0], dates)  # +5%
        _seed(db, "DOWNCO", [100.0, 95.0], dates)  # -5%

        market_snapshot._build_cached.cache_clear()
        after = market_snapshot.build_snapshot(db)

        assert after.up_count == before.up_count + 1
        assert after.down_count == before.down_count + 1
        assert after.moved_2pct_count == before.moved_2pct_count + 2

    def test_golden_cross_detects_close_crossing_above_sma200(self, db, monkeypatch):
        monkeypatch.setattr(market_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
        market_snapshot._build_cached.cache_clear()
        before = market_snapshot.build_snapshot(db)

        dates = _recent_trade_dates(db, 2)
        crossing_id = _seed(db, "CROSSCO", [95.0, 105.0], dates)
        _seed_indicator(db, crossing_id, dates[0], sma_200=100.0)
        _seed_indicator(db, crossing_id, dates[1], sma_200=100.0)

        no_cross_id = _seed(db, "FLATCO", [105.0, 106.0], dates)
        _seed_indicator(db, no_cross_id, dates[0], sma_200=100.0)
        _seed_indicator(db, no_cross_id, dates[1], sma_200=100.0)

        market_snapshot._build_cached.cache_clear()
        after = market_snapshot.build_snapshot(db)

        # Only CROSSCO (below sma200 yesterday, above today) is a new cross --
        # FLATCO was already above sma200 yesterday, so it must not count.
        assert after.golden_cross_count == before.golden_cross_count + 1

    def test_volume_breakout_requires_ratio_over_threshold(self, db, monkeypatch):
        monkeypatch.setattr(market_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
        market_snapshot._build_cached.cache_clear()
        before = market_snapshot.build_snapshot(db)

        dates = _recent_trade_dates(db, 2)
        breakout_id = _seed(db, "VOLCO", [50.0, 51.0], dates, volumes=[100_000, 400_000])
        _seed_indicator(db, breakout_id, dates[1], volume_sma_20=100_000)

        normal_id = _seed(db, "NORMCO", [50.0, 51.0], dates, volumes=[100_000, 150_000])
        _seed_indicator(db, normal_id, dates[1], volume_sma_20=100_000)

        market_snapshot._build_cached.cache_clear()
        after = market_snapshot.build_snapshot(db)

        # Only VOLCO clears the 3x ratio -- NORMCO's 1.5x must not count.
        assert after.volume_breakout_count == before.volume_breakout_count + 1

    def test_repeat_call_same_as_of_is_a_cache_hit(self, db, monkeypatch):
        monkeypatch.setattr(market_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
        market_snapshot._build_cached.cache_clear()

        dates = _recent_trade_dates(db, 2)
        _seed(db, "CACHECO", [100.0, 101.0], dates)

        hits_before = market_snapshot._build_cached.cache_info().hits
        market_snapshot.build_snapshot(db)
        market_snapshot.build_snapshot(db)
        assert market_snapshot._build_cached.cache_info().hits == hits_before + 1
