"""Tests for app/services/crossover_loader.py.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, using throwaway
instruments so nothing here depends on real market data.
Run with: pytest tests/test_crossover_loader.py -v
"""

import contextlib
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Instrument
from app.services import crossover_loader


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


def _seed_instrument(db: Session, symbol: str, closes: list[float], start: date) -> int:
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True)
    db.add(inst)
    db.flush()
    for i, close in enumerate(closes):
        d = start + timedelta(days=i)
        db.add(
            DailyPrice(
                instrument_id=inst.id, trade_date=d, open=close, high=close, low=close,
                close=close, adjusted_close=close, volume=1000,
            )
        )
    db.flush()
    return inst.id


class TestResolveWindow:
    def test_cutoff_and_as_of_span_n_distinct_dates(self, db, monkeypatch):
        # crossover_loader opens its OWN connection (see module docstring),
        # not the test's SAVEPOINT-backed session -- point its engine calls at
        # the same connection so seeded rows are visible without committing.
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        start = date(2026, 1, 1)
        _seed_instrument(db, "AAA", [100.0] * 10, start)

        cutoff, as_of = crossover_loader.resolve_window(5)
        assert as_of == start + timedelta(days=9)
        assert cutoff == start + timedelta(days=5)


class TestLoadWide:
    def test_pivots_and_forward_fills_within_tolerance(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        start = date(2026, 1, 1)
        id_a = _seed_instrument(db, "AAA", [10.0, 11.0, 12.0], start)
        id_b = _seed_instrument(db, "BBB", [20.0, 21.0, 22.0], start)

        wide = crossover_loader.load_wide(start)
        assert list(wide.columns) == sorted([id_a, id_b])
        assert wide.loc[start, id_a] == 10.0
        assert wide.loc[start + timedelta(days=2), id_b] == 22.0

    def test_excludes_inactive_instruments(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        start = date(2026, 1, 1)
        inst = Instrument(symbol="ZZZ", exchange="NSE", company_name="ZZZ", is_active=False)
        db.add(inst)
        db.flush()
        db.add(DailyPrice(instrument_id=inst.id, trade_date=start, open=1, high=1, low=1, close=1, adjusted_close=1, volume=1))
        db.flush()

        wide = crossover_loader.load_wide(start)
        assert inst.id not in wide.columns


class TestRunScan:
    def test_finds_a_known_crossover_and_counts_it(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        start = date(2026, 1, 1)
        # 8 flat bars then a jump -- fast(2) crosses above slow(3) on the move.
        # NOTE: brief bug fix -- the brief's literal here was
        # [10, 10, 10, 10, 10, 10, 30, 30, 30] (6 flat + 3 elevated), which
        # contradicts its own comment and, verified with pandas directly,
        # produces NO crossing signal on the last bar (the crossing event
        # happens on day 7; by day 9 diff has settled back to 0, and
        # scan_last_bar only reports a signal when the crossing occurs on
        # the last bar). [10]*8 + [30] matches the comment and actually
        # crosses on the last bar.
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], start)
        flat_id = _seed_instrument(db, "FLAT", [50.0] * 9, start)

        result = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")

        assert crossing_id in result.matches.index
        assert result.matches[crossing_id] == "crossed_above"
        assert flat_id not in result.matches.index
        assert result.evaluated == 2
        assert result.skipped_stale == 0

    def test_direction_filter_excludes_non_matching_signals(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        start = date(2026, 1, 1)
        # See note in test_finds_a_known_crossover_and_counts_it -- fixed to
        # [10]*8 + [30] so this instrument has a real crossed_above signal
        # to be correctly filtered out (not trivially absent).
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], start)

        result = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="crossed_below")
        assert crossing_id not in result.matches.index

    def test_repeat_call_same_as_of_is_a_cache_hit(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        start = date(2026, 1, 1)
        _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], start)

        first = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")
        second = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")
        assert first.cached is False
        assert second.cached is True

    def test_counts_insufficient_history_separately_from_stale(self, db, monkeypatch):
        # _connect() is used as `with _connect() as conn:` in production code,
        # which closes a fresh engine connection after each use -- wrapping
        # the test's shared, SAVEPOINT-backed connection in nullcontext makes
        # that `with` a no-op instead of closing (and breaking) the fixture.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        start = date(2026, 1, 1)
        # Only 2 bars -- can't form a slow=3 SMA at all.
        _seed_instrument(db, "SHORT", [10.0, 11.0], start)

        result = crossover_loader.run_scan(db, fast=1, slow=3, ma_type="sma", direction="any")
        assert result.evaluated == 1
        assert result.skipped_insufficient_history == 1
        assert result.skipped_stale == 0
