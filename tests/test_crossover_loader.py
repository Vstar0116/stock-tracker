"""Tests for app/services/crossover_loader.py.

Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, using throwaway
instruments so nothing here depends on real market data for correctness.

IMPORTANT: this dev database now carries real, backfilled market data
(~7,500 active instruments, ~3.9M daily_prices rows through the current
as_of). resolve_window/load_wide/the active-instrument count are legitimately
whole-market queries by design (that's the market-wide scan's whole point),
so they will see that real data alongside whatever a test seeds -- these
tests anchor seeded rows to the REAL, current trade-date calendar (via
_recent_trade_dates) and assert relative outcomes (their own seeded
instrument's presence/value, or before/after deltas) rather than absolute
counts or index equality that assumed an empty table.

Run with: pytest tests/test_crossover_loader.py -v
"""

import contextlib
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Instrument
from app.services import crossover_loader
from app.services.crossover import STALE_TOLERANCE_DAYS


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
    """The n most recent distinct trade_dates already present in daily_prices,
    ascending. Anchoring seeded rows to real, already-existing trade dates
    (rather than a hardcoded calendar range) means the seed never introduces
    NEW distinct dates that could shift as_of/cutoff for every other query
    sharing this table, and the last date returned is always the current
    as_of (MAX(trade_date))."""
    rows = db.execute(
        text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"),
        {"n": n},
    ).fetchall()
    return sorted(r[0] for r in rows)


def _seed_instrument(db: Session, symbol: str, closes: list[float], dates: list[date]) -> int:
    assert len(closes) == len(dates)
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=symbol, is_active=True)
    db.add(inst)
    db.flush()
    for d, close in zip(dates, closes):
        db.add(
            DailyPrice(
                instrument_id=inst.id, trade_date=d, open=close, high=close, low=close,
                close=close, adjusted_close=close, volume=1000,
            )
        )
    db.flush()
    return inst.id


def _active_count(db: Session) -> int:
    return db.execute(text("SELECT COUNT(*) FROM instruments WHERE is_active")).scalar_one()


def _clear_caches():
    crossover_loader._scan_cached.cache_clear()
    crossover_loader._load_wide_cached.cache_clear()


class TestResolveWindow:
    def test_cutoff_and_as_of_span_n_distinct_dates(self, db, monkeypatch):
        # crossover_loader opens its OWN connection (see module docstring),
        # not the test's SAVEPOINT-backed session -- point its engine calls at
        # the same connection so seeded rows are visible without committing.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))

        # resolve_window is a genuinely whole-market query (there is no
        # per-instrument filter to seed against), so this test verifies it
        # directly against the same live calendar rather than a hardcoded
        # date range assumed to be the only data in the table.
        n = 5
        expected = _recent_trade_dates(db, n)

        cutoff, as_of = crossover_loader.resolve_window(n)
        assert as_of == expected[-1]
        assert cutoff == expected[0]


class TestLoadWide:
    def test_pivots_and_forward_fills_within_tolerance(self, db, monkeypatch):
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        dates = _recent_trade_dates(db, 3)
        id_a = _seed_instrument(db, "AAA", [10.0, 11.0, 12.0], dates)
        id_b = _seed_instrument(db, "BBB", [20.0, 21.0, 22.0], dates)

        wide = crossover_loader.load_wide(dates[0])
        # The real market's own active instruments are legitimately present
        # too (whole-market query) -- assert our seeded columns/values
        # specifically rather than the exact column set.
        assert id_a in wide.columns
        assert id_b in wide.columns
        assert wide.loc[dates[0], id_a] == 10.0
        assert wide.loc[dates[2], id_b] == 22.0

    def test_excludes_inactive_instruments(self, db, monkeypatch):
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
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        _clear_caches()

        before_active = _active_count(db)
        dates = _recent_trade_dates(db, 9)
        # 8 flat bars then a jump on the real market's last 9 trading days --
        # fast(2) crosses above slow(3) on the final bar.
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], dates)
        flat_id = _seed_instrument(db, "FLAT", [50.0] * 9, dates)

        result = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")

        assert crossing_id in result.matches.index
        assert result.matches[crossing_id] == "crossed_above"
        assert flat_id not in result.matches.index
        # evaluated is a whole-market count now -- assert the delta our own
        # two new active instruments contributed, not an absolute total.
        assert result.evaluated == before_active + 2

    def test_direction_filter_excludes_non_matching_signals(self, db, monkeypatch):
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        _clear_caches()

        dates = _recent_trade_dates(db, 9)
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], dates)

        below = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="crossed_below")
        assert crossing_id not in below.matches.index

        # Prove the exclusion above is really the direction filter at work
        # (not the seeded instrument simply being absent from the scan
        # window) by confirming it DOES show up, with the expected signal,
        # under "crossed_above" / "any".
        _clear_caches()
        above = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="crossed_above")
        assert crossing_id in above.matches.index
        assert above.matches[crossing_id] == "crossed_above"

    def test_repeat_call_same_as_of_is_a_cache_hit(self, db, monkeypatch):
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        _clear_caches()

        dates = _recent_trade_dates(db, 9)
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 10, 10, 30], dates)

        first = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")
        second = crossover_loader.run_scan(db, fast=2, slow=3, ma_type="sma", direction="any")
        assert first.cached is False
        assert second.cached is True
        # Exercise the seeded data through both the cold and cached path --
        # not just the cache_info() bookkeeping.
        assert crossing_id in first.matches.index
        assert crossing_id in second.matches.index
        assert first.matches[crossing_id] == second.matches[crossing_id] == "crossed_above"

    def test_counts_insufficient_history_separately_from_stale(self, db, monkeypatch):
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        _clear_caches()
        before = crossover_loader.run_scan(db, fast=1, slow=3, ma_type="sma", direction="any")

        # Only 2 bars -- can't form a slow=3 SMA at all.
        dates = _recent_trade_dates(db, 2)
        short_id = _seed_instrument(db, "SHORT", [10.0, 11.0], dates)

        _clear_caches()
        after = crossover_loader.run_scan(db, fast=1, slow=3, ma_type="sma", direction="any")

        assert after.evaluated == before.evaluated + 1
        assert after.skipped_insufficient_history == before.skipped_insufficient_history + 1
        assert after.skipped_stale == before.skipped_stale
        assert short_id not in after.matches.index

    def test_gap_exactly_at_stale_tolerance_is_forward_filled_not_insufficient(self, db, monkeypatch):
        # Regression test for a review finding: _scan_cached used to size its
        # query window as exactly warmup_bars(slow, ma_type), which for a
        # small slow (e.g. 3, giving warmup_bars=4) can be narrower than
        # STALE_TOLERANCE_DAYS. An instrument whose last bar is within
        # tolerance but older than that narrow window never comes back from
        # the query at all -- it's not a column in `wide` -- so it silently
        # fell into `never_loaded` and was miscounted as
        # skipped_insufficient_history instead of being forward-filled and
        # scored normally, even though it has plenty of history.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))

        _clear_caches()
        before_small = crossover_loader.run_scan(db, fast=1, slow=3, ma_type="sma", direction="any")
        _clear_caches()
        before_large = crossover_loader.run_scan(db, fast=1, slow=10, ma_type="sma", direction="any")

        dates = _recent_trade_dates(db, 13)
        # FULL trades every one of the real market's last 13 trading days,
        # establishing full history through as_of.
        _seed_instrument(db, "FULL", [100.0] * 13, dates)
        # GAP's last bar is exactly STALE_TOLERANCE_DAYS trading days before
        # as_of -- the boundary case that must still be forward-filled, not
        # dropped.
        gap_len = 13 - STALE_TOLERANCE_DAYS
        gap_id = _seed_instrument(db, "GAP", [10.0] * gap_len, dates[:gap_len])

        # Small slow: warmup_bars(3, "sma") = 4, narrower than the tolerance
        # window before the fix.
        _clear_caches()
        small = crossover_loader.run_scan(db, fast=1, slow=3, ma_type="sma", direction="any")
        assert small.evaluated == before_small.evaluated + 2
        assert small.skipped_stale == before_small.skipped_stale
        assert small.skipped_insufficient_history == before_small.skipped_insufficient_history
        assert gap_id not in small.matches.index  # flat prices, no crossover -- just checking it wasn't skipped

        # Larger slow: warmup_bars(10, "sma") = 11, already wider than the
        # tolerance window -- this case worked correctly even before the
        # fix, and must keep working the same way.
        _clear_caches()
        large = crossover_loader.run_scan(db, fast=1, slow=10, ma_type="sma", direction="any")
        assert large.evaluated == before_large.evaluated + 2
        assert large.skipped_stale == before_large.skipped_stale
        assert large.skipped_insufficient_history == before_large.skipped_insufficient_history
        assert gap_id not in large.matches.index
