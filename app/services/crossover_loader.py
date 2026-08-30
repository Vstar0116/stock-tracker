"""DB-backed loading and caching for the market-wide crossover scan
(app/services/crossover.py::scan_last_bar). Opens its own connection via
_connect() rather than taking one through FastAPI's dependency injection --
functools.lru_cache needs plain, hashable arguments, and a request-scoped
Session can't safely be reused across requests as one.

The nightly pipeline is the only thing that changes daily_prices, so a
scan result is valid until `as_of` (the market's latest trade_date) moves --
putting as_of in the cache key makes invalidation automatic, no TTL needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.services.crossover import STALE_TOLERANCE_DAYS, MaType, scan_last_bar, warmup_bars
from app.services.screening import latest_trade_date

Direction = str  # "crossed_above" | "crossed_below" | "any"


def _connect():
    """Indirection point so tests can substitute the SAVEPOINT-backed test
    connection (see tests/test_crossover_loader.py) without this module
    otherwise ever taking a connection as a parameter."""
    return engine.connect()


def resolve_window(n_bars: int) -> tuple[date, date]:
    """One cheap query for the market calendar. All instruments share a
    trading calendar, so the Nth-most-recent distinct trade_date is a valid
    cutoff for every instrument at once. Returns (cutoff, as_of)."""
    with _connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date FROM daily_prices
                    ORDER BY trade_date DESC LIMIT :n
                ) t ORDER BY trade_date ASC
                """
            ),
            {"n": n_bars},
        ).fetchall()
    if not rows:
        raise ValueError("no price data loaded yet")
    return rows[0][0], rows[-1][0]


def load_wide(cutoff: date) -> pd.DataFrame:
    """One range scan for the whole market, pivoted to trade_date x instrument_id."""
    with _connect() as conn:
        long = pd.read_sql(
            text(
                """
                SELECT p.instrument_id, p.trade_date, p.adjusted_close
                FROM daily_prices p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE i.is_active AND p.trade_date >= :cutoff
                """
            ),
            conn,
            params={"cutoff": cutoff},
        )
    wide = long.pivot(index="trade_date", columns="instrument_id", values="adjusted_close").sort_index()
    return wide.ffill(limit=STALE_TOLERANCE_DAYS)


# maxsize=1, not more: each entry is the whole active market pivoted to
# bars x instruments, so this cache is the app's single largest memory
# consumer and it lives in every gunicorn worker. `as_of` is the same for
# all users on a given trading day, so one entry still serves the common
# case (everyone on default params); a parameter sweep now costs a re-query
# instead of pinning several hundred MB on a 512MB instance.
@lru_cache(maxsize=1)
def _load_wide_cached(n_bars: int, as_of: date) -> pd.DataFrame:
    cutoff, _ = resolve_window(n_bars)
    return load_wide(cutoff)


@dataclass
class _RawScan:
    signals: pd.Series
    evaluated: int
    skipped_insufficient_history: int
    skipped_stale: int


@lru_cache(maxsize=64)
def _scan_cached(fast: int, slow: int, ma_type: MaType, as_of: date) -> _RawScan:
    # Query window must never be narrower than the staleness tolerance --
    # otherwise an instrument whose last bar is within tolerance but older
    # than the warmup window never even comes back from the query (it's not
    # a column in `wide` at all), and gets miscounted as insufficient
    # history instead of being forward-filled and scored normally.
    n_bars = max(warmup_bars(slow, ma_type), STALE_TOLERANCE_DAYS + 1)
    wide = _load_wide_cached(n_bars, as_of)

    with _connect() as conn:
        total_active = conn.execute(text("SELECT COUNT(*) FROM instruments WHERE is_active")).scalar_one()

    stale_mask = wide.iloc[-1].isna()
    skipped_stale = int(stale_mask.sum())

    signals = scan_last_bar(wide, fast, slow, ma_type)

    # ponytail: recomputes the slow MA a second time (scan_last_bar already
    # computed it internally but doesn't expose it) purely to build the
    # insufficient-history count. A single extra rolling/ewm pass over the
    # trimmed wide frame is cheap relative to the query itself; upgrade path
    # is a scan_last_bar variant that also returns this mask, if this ever
    # shows up in profiling.
    from app.services.crossover import _moving_average

    slow_ma_last = _moving_average(wide, slow, ma_type).iloc[-1]
    no_history_mask = slow_ma_last.isna() & ~stale_mask
    never_loaded = total_active - wide.shape[1]
    skipped_insufficient_history = int(no_history_mask.sum()) + never_loaded

    return _RawScan(
        signals=signals,
        evaluated=total_active,
        skipped_insufficient_history=skipped_insufficient_history,
        skipped_stale=skipped_stale,
    )


@dataclass
class ScanResult:
    as_of: date
    matches: pd.Series
    evaluated: int
    skipped_insufficient_history: int
    skipped_stale: int
    elapsed_ms: int
    cached: bool


def run_scan(db: Session, fast: int, slow: int, ma_type: MaType, direction: Direction) -> ScanResult:
    """The function the API route calls directly. `db` is only used for the
    cheap, uncached as_of freshness check -- the expensive, cached work below
    opens its own connection (see _connect)."""
    as_of = latest_trade_date(db)
    if as_of is None:
        raise ValueError("no price data loaded yet")

    t0 = time.perf_counter()
    hits_before = _scan_cached.cache_info().hits
    raw = _scan_cached(fast, slow, ma_type, as_of)
    cached = _scan_cached.cache_info().hits > hits_before
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    matches = raw.signals if direction == "any" else raw.signals[raw.signals == direction]

    return ScanResult(
        as_of=as_of,
        matches=matches,
        evaluated=raw.evaluated,
        skipped_insufficient_history=raw.skipped_insufficient_history,
        skipped_stale=raw.skipped_stale,
        elapsed_ms=elapsed_ms,
        cached=cached,
    )
