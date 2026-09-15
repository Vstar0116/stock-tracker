"""Tests for app/services/dashboard_snapshot.py.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, same pattern as
tests/test_market_snapshot.py. The whole-market pieces (breadth, heatmap)
share that file's "assert deltas, not membership" convention since the real
dev DB carries real market data; the per-user pieces (movers, fired screens)
are scoped to a freshly seeded user so exact-count assertions are safe there.

Run with: pytest tests/test_dashboard_snapshot.py -v
"""

import contextlib
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import Alert, DailyPrice, Instrument, Screen, User, Watchlist, WatchlistItem
from app.services import dashboard_snapshot, market_snapshot


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


def _seed_instrument(db: Session, symbol: str, closes: list[float], dates: list[date], sector: str | None = None, industry: str | None = None) -> int:
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True, sector=sector, industry=industry)
    db.add(inst)
    db.flush()
    for d, close in zip(dates, closes):
        db.add(DailyPrice(instrument_id=inst.id, trade_date=d, open=close, high=close, low=close, close=close, adjusted_close=close, volume=100_000))
    db.flush()
    return inst.id


def _seed_user(db: Session, email: str) -> int:
    user = User(email=email, name="Dashboard Test", password_hash="x")
    db.add(user)
    db.flush()
    return user.id


def _patch_both_connects(monkeypatch, db):
    monkeypatch.setattr(market_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
    monkeypatch.setattr(dashboard_snapshot, "_connect", lambda: contextlib.nullcontext(db.connection()))
    market_snapshot._build_cached.cache_clear()
    dashboard_snapshot._heatmap_cached.cache_clear()


class TestBuildDashboard:
    def test_watchlist_count_reflects_the_users_own_watchlists_only(self, db, monkeypatch):
        _patch_both_connects(monkeypatch, db)
        user_id = _seed_user(db, "wl-count@example.com")
        other_id = _seed_user(db, "wl-count-other@example.com")
        db.add(Watchlist(user_id=user_id, name="Core"))
        db.add(Watchlist(user_id=user_id, name="Speculative"))
        db.add(Watchlist(user_id=other_id, name="Not mine"))
        db.flush()

        out = dashboard_snapshot.build_dashboard(db, user_id)

        assert out.watchlist_count == 2

    def test_movers_dedupe_across_watchlists_and_rank_by_abs_change(self, db, monkeypatch):
        _patch_both_connects(monkeypatch, db)
        user_id = _seed_user(db, "movers@example.com")
        dates = _recent_trade_dates(db, 2)
        big_mover = _seed_instrument(db, "BIGMOVE", [100.0, 110.0], dates)  # +10%
        small_mover = _seed_instrument(db, "SMALLMOVE", [100.0, 101.0], dates)  # +1%

        wl_a = Watchlist(user_id=user_id, name="A")
        wl_b = Watchlist(user_id=user_id, name="B")
        db.add_all([wl_a, wl_b])
        db.flush()
        # BIGMOVE sits in both lists -- must appear once in the result, not twice.
        db.add(WatchlistItem(watchlist_id=wl_a.id, instrument_id=big_mover))
        db.add(WatchlistItem(watchlist_id=wl_b.id, instrument_id=big_mover))
        db.add(WatchlistItem(watchlist_id=wl_a.id, instrument_id=small_mover))
        db.flush()

        out = dashboard_snapshot.build_dashboard(db, user_id)

        symbols = [m.symbol for m in out.movers]
        assert symbols.count("BIGMOVE") == 1
        assert symbols.index("BIGMOVE") < symbols.index("SMALLMOVE")  # ranked by |change|, big first

    def test_fired_screens_counts_only_the_current_users_screens_today(self, db, monkeypatch):
        _patch_both_connects(monkeypatch, db)
        user_id = _seed_user(db, "fired@example.com")
        other_id = _seed_user(db, "fired-other@example.com")
        dates = _recent_trade_dates(db, 1)
        as_of = dates[0]
        inst_id = _seed_instrument(db, "FIREDCO", [100.0], [as_of])

        mine = Screen(user_id=user_id, name="Mine", definition={"type": "compare", "op": "gt", "field": "close", "value": 1})
        theirs = Screen(user_id=other_id, name="Theirs", definition={"type": "compare", "op": "gt", "field": "close", "value": 1})
        db.add_all([mine, theirs])
        db.flush()
        db.add(Alert(screen_id=mine.id, instrument_id=inst_id, trade_date=as_of, snapshot={}))
        db.add(Alert(screen_id=theirs.id, instrument_id=inst_id, trade_date=as_of, snapshot={}))
        db.flush()

        out = dashboard_snapshot.build_dashboard(db, user_id)

        names = {f.name for f in out.fired_screens}
        assert "Mine" in names
        assert "Theirs" not in names

    def test_heatmap_groups_by_industry_and_moves_with_seeded_data(self, db, monkeypatch):
        _patch_both_connects(monkeypatch, db)
        user_id = _seed_user(db, "heat@example.com")
        dates = _recent_trade_dates(db, 2)
        # min-count threshold is 3 -- seed enough rows in one industry that
        # the group survives the HAVING filter and its average is exactly
        # computable (all three instruments move identically).
        for i in range(3):
            _seed_instrument(db, f"HEATCO{i}", [100.0, 120.0], dates, industry="Test Heatmap Industry")

        out = dashboard_snapshot.build_dashboard(db, user_id)

        entry = next((h for h in out.heatmap if h.sector == "Test Heatmap Industry"), None)
        assert entry is not None
        assert entry.count == 3
        assert entry.change_pct == pytest.approx(20.0)
