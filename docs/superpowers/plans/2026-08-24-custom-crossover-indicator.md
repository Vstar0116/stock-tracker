# Custom MA Crossover Indicator + Custom Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick any fast/slow moving-average period pair (SMA or EMA) and (1) view the crossover on one stock's price history instantly, and (2) scan the whole market for stocks currently showing that crossover, on demand.

**Architecture:** A pure pandas calculation layer (`app/services/crossover.py`) shared by two consumers — a per-instrument path (full history, computed instantly) and a market-wide path (a trimmed window loaded in one query, computed vectorized across all instruments, cached by `(fast, slow, ma_type, as_of)` so the nightly pipeline is the only thing that ever invalidates it). Two new FastAPI routes expose both. No DB schema changes.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy Core (raw connection for the loader, not the ORM — this is a read-heavy analytical query, not entity CRUD), pandas, pytest. React/TypeScript on the frontend, matching existing patterns in `frontend/src/pages/`.

**Spec:** `docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md`

## Global Constraints

- No changes to the `indicators` table schema or the nightly `compute_indicators` job.
- No changes to the Screener / `ScreenRule` DSL — this is an additive, separate feature.
- No new DB migration. No job queue, no async workers, no polling.
- No advice/signal language in UI copy — results are historical state ("crossed above as of `trade_date` X"), never a recommendation.
- Both MAs in a crossover pair use the same `ma_type` — mixed SMA/EMA pairs are not supported.
- Periods: `1 <= fast < slow <= 400`, enforced identically wherever periods are accepted.
- Only `adjusted_close` is ever used — never raw `close` (same rule as every other indicator in this app).
- Crossover fires strictly: `crossed_above` at bar *t* iff `diff[t] > 0 and diff[t-1] <= 0`; `crossed_below` iff `diff[t] < 0 and diff[t-1] >= 0`; an exactly-equal bar emits no signal.
- `as_of` for the scan is the market's latest `trade_date` (`MAX(trade_date)` across `daily_prices`), not any one instrument's own latest bar.
- Reuse `sma()`/`ema()` from `app/services/indicators.py` rather than reimplementing moving-average math — both already work unchanged on a wide DataFrame as well as a Series (pandas `.rolling()`/`.ewm()` operate column-wise), so no new MA helper is needed.

---

### Task 1: Pure crossover calculation — single instrument

**Files:**
- Create: `app/services/crossover.py`
- Test: `tests/test_crossover.py`

**Interfaces:**
- Consumes: `sma(prices: pd.Series, window: int) -> pd.Series` and `ema(prices: pd.Series, window: int) -> pd.Series` from `app/services/indicators.py` (existing, unchanged).
- Produces:
  - `MAX_PERIOD: int = 400`
  - `STALE_TOLERANCE_DAYS: int = 5`
  - `validate_periods(fast: int, slow: int) -> None` — raises `ValueError` on any invalid pair.
  - `warmup_bars(slow: int, ma_type: Literal["sma", "ema"]) -> int`
  - `compute_crossover(prices: pd.DataFrame, fast: int, slow: int, ma_type: Literal["sma", "ema"]) -> pd.DataFrame` — indexed by `trade_date`, columns `fast`, `slow`, `signal` (`"crossed_above" | "crossed_below" | None`). `prices` must have an `adjusted_close` column, indexed by `trade_date` ascending — the exact shape `app/jobs/compute_indicators.py::load_price_history` already returns.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_crossover.py
"""Tests for app/services/crossover.py using hand-verifiable inputs.

Pure pandas functions -- no database needed.
Run with: pytest tests/test_crossover.py -v
"""

import pandas as pd
import pytest

from app.services.crossover import MAX_PERIOD, compute_crossover, validate_periods, warmup_bars


def _prices(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"adjusted_close": closes}, index=pd.Index(dates, name="trade_date"))


class TestValidatePeriods:
    def test_accepts_valid_pair(self):
        validate_periods(9, 21)  # must not raise

    def test_rejects_fast_equal_slow(self):
        with pytest.raises(ValueError):
            validate_periods(20, 20)

    def test_rejects_fast_greater_than_slow(self):
        with pytest.raises(ValueError):
            validate_periods(50, 20)

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            validate_periods(0, 20)

    def test_rejects_over_ceiling(self):
        with pytest.raises(ValueError):
            validate_periods(9, MAX_PERIOD + 1)


class TestWarmupBars:
    def test_sma_is_slow_plus_one(self):
        assert warmup_bars(50, "sma") == 51

    def test_ema_has_a_generous_floor(self):
        assert warmup_bars(9, "ema") == 250  # max(250, 6*9) == 250

    def test_ema_scales_past_the_floor(self):
        assert warmup_bars(100, "ema") == 600  # max(250, 6*100) == 600


class TestComputeCrossoverSMA:
    def test_clean_crossover_above(self):
        # fast=2, slow=3. Prices engineered so fast SMA overtakes slow SMA
        # partway through: [10,10,10, 10,10,10, 20,20,20]
        prices = _prices([10, 10, 10, 10, 10, 10, 20, 20, 20])
        result = compute_crossover(prices, fast=2, slow=3, ma_type="sma")
        signals = result["signal"].dropna()
        assert (signals == "crossed_above").any()
        assert not (signals == "crossed_below").any()

    def test_clean_crossover_below(self):
        prices = _prices([20, 20, 20, 20, 20, 20, 10, 10, 10])
        result = compute_crossover(prices, fast=2, slow=3, ma_type="sma")
        signals = result["signal"].dropna()
        assert (signals == "crossed_below").any()
        assert not (signals == "crossed_above").any()

    def test_flat_series_never_signals(self):
        prices = _prices([50.0] * 10)
        result = compute_crossover(prices, fast=2, slow=4, ma_type="sma")
        assert result["signal"].isna().all()

    def test_monotonic_rise_signals_at_most_once(self):
        prices = _prices([float(i) for i in range(1, 15)])
        result = compute_crossover(prices, fast=2, slow=5, ma_type="sma")
        signals = result["signal"].dropna()
        assert len(signals) <= 1

    def test_insufficient_history_is_nan_not_partial(self):
        prices = _prices([float(i) for i in range(1, 10)])  # only 9 bars
        result = compute_crossover(prices, fast=5, slow=200, ma_type="sma")
        assert result["slow"].isna().all()
        assert result["signal"].isna().all()

    def test_exact_equality_bar_emits_no_signal(self):
        # fast and slow SMA land on the exact same value at bar 3 (both windows
        # cover only the constant run), then a real move afterward gives the
        # first strict crossover -- the equal bar itself must be None.
        prices = _prices([10, 10, 10, 10, 30, 30, 30])
        result = compute_crossover(prices, fast=1, slow=1, ma_type="sma")
        # fast==slow window means diff is always 0 -- degenerate but must
        # never emit a signal (equal counts as not-yet-crossed at every bar).
        assert result["signal"].isna().all()


class TestComputeCrossoverEMA:
    def test_ema_crossover_detected(self):
        prices = _prices([10.0] * 10 + [30.0] * 10)
        result = compute_crossover(prices, fast=3, slow=6, ma_type="ema")
        signals = result["signal"].dropna()
        assert (signals == "crossed_above").any()

    def test_ema_truncation_matches_full_history_on_last_bar(self):
        # The empirical check on warmup_bars' seed-error argument: EMA computed
        # over just the warmup window should match EMA over a much longer
        # history to within a tight tolerance, on the last bar.
        from app.services.crossover import warmup_bars
        from app.services.indicators import ema

        full = [100.0 + (i % 7) - 3 for i in range(2000)]
        prices_full = _prices(full)
        w = warmup_bars(50, "ema")
        prices_trunc = _prices(full[-w:])

        full_ema = ema(prices_full["adjusted_close"], 50).iloc[-1]
        trunc_ema = ema(prices_trunc["adjusted_close"], 50).iloc[-1]
        assert trunc_ema == pytest.approx(full_ema, rel=1e-4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crossover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.crossover'`

- [ ] **Step 3: Implement `app/services/crossover.py`**

```python
"""Custom moving-average crossover: user-chosen fast/slow periods and MA
type (SMA or EMA), computed on demand rather than precomputed and stored.

Two entry points share this module: compute_crossover (below, one
instrument, full series) and scan_last_bar (Task 2, market-wide, vectorized
across every instrument at once). Both must agree bar-for-bar -- see
tests/test_crossover.py's TestParity in Task 2.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from app.services.indicators import ema, sma

MaType = Literal["sma", "ema"]
Signal = Literal["crossed_above", "crossed_below"]

MAX_PERIOD = 400
STALE_TOLERANCE_DAYS = 5


def validate_periods(fast: int, slow: int) -> None:
    """Raise ValueError on any invalid period pair. Callers map this to a 422."""
    if fast < 1 or slow < 1:
        raise ValueError("fast and slow must be positive integers")
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be less than slow ({slow})")
    if slow > MAX_PERIOD:
        raise ValueError(f"slow must not exceed {MAX_PERIOD}")


def warmup_bars(slow: int, ma_type: MaType) -> int:
    """Minimum trailing bars needed for the last two MA values to be sound.

    SMA is a finite window: slow + 1 bars give exactly two consecutive
    values. EMA is recursive -- its value depends on the whole series
    through a decaying weight. Truncating at k bars leaves a seed error of
    roughly (1 - 2/(slow+1))^k; at k = 6*slow that's under 1e-5 relative,
    far below any price resolution that could flip a crossover (see
    TestComputeCrossoverEMA.test_ema_truncation_matches_full_history_on_last_bar
    for the empirical check). The 250 floor keeps short windows generously
    warmed.
    """
    return slow + 1 if ma_type == "sma" else max(250, 6 * slow)


def _moving_average(x: pd.Series | pd.DataFrame, window: int, ma_type: MaType):
    return sma(x, window) if ma_type == "sma" else ema(x, window)


def _signal_from_diff(diff: pd.Series, prev: pd.Series) -> pd.Series:
    signal = pd.Series(None, index=diff.index, dtype=object)
    signal[(diff > 0) & (prev <= 0)] = "crossed_above"
    signal[(diff < 0) & (prev >= 0)] = "crossed_below"
    return signal


def compute_crossover(prices: pd.DataFrame, fast: int, slow: int, ma_type: MaType) -> pd.DataFrame:
    """Single instrument, full series. `prices` indexed by trade_date with an
    adjusted_close column (the exact shape compute_indicators.load_price_history
    returns). Returns a DataFrame with the same index and columns fast/slow/signal."""
    validate_periods(fast, slow)
    close = prices["adjusted_close"].astype(float)
    fast_ma = _moving_average(close, fast, ma_type)
    slow_ma = _moving_average(close, slow, ma_type)
    diff = fast_ma - slow_ma
    prev = diff.shift(1)
    signal = _signal_from_diff(diff, prev)
    return pd.DataFrame({"fast": fast_ma, "slow": slow_ma, "signal": signal}, index=prices.index)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crossover.py -v`
Expected: PASS (all `TestValidatePeriods`, `TestWarmupBars`, `TestComputeCrossoverSMA`, `TestComputeCrossoverEMA` cases)

- [ ] **Step 5: Commit**

```bash
git add app/services/crossover.py tests/test_crossover.py
git commit -m "feat: add pure MA crossover calculation for a single instrument"
```

---

### Task 2: Vectorized market-wide scan

**Files:**
- Modify: `app/services/crossover.py` (add `scan_last_bar`)
- Modify: `tests/test_crossover.py` (add parity test)

**Interfaces:**
- Consumes: `compute_crossover` (Task 1), `_moving_average` (Task 1, module-private).
- Produces: `scan_last_bar(wide: pd.DataFrame, fast: int, slow: int, ma_type: MaType) -> pd.Series` — `wide` is `trade_date x instrument_id` of `adjusted_close`; returns a Series indexed by `instrument_id` holding the signal on the final row only, non-signaling instruments dropped. This is what Task 3's loader/scan orchestration calls.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_crossover.py

import numpy as np


class TestScanLastBarParity:
    def test_matches_per_instrument_compute_crossover(self):
        # Build a wide frame of several independent instruments, each with
        # its own randomized-but-seeded walk, and confirm scan_last_bar's
        # vectorized result agrees with running compute_crossover on each
        # column separately -- this is what keeps the instant endpoint and
        # the market-wide scan from ever disagreeing.
        from app.services.crossover import compute_crossover, scan_last_bar

        rng = np.random.default_rng(42)
        n_bars, n_instruments = 80, 12
        dates = pd.date_range("2026-01-01", periods=n_bars, freq="D")

        wide = pd.DataFrame(
            {i: 100 + np.cumsum(rng.normal(0, 1, n_bars)) for i in range(n_instruments)},
            index=pd.Index(dates, name="trade_date"),
        )

        for ma_type in ("sma", "ema"):
            for fast, slow in [(3, 8), (5, 20)]:
                vectorized = scan_last_bar(wide, fast, slow, ma_type)
                for instrument_id in wide.columns:
                    single = compute_crossover(
                        wide[[instrument_id]].rename(columns={instrument_id: "adjusted_close"}),
                        fast, slow, ma_type,
                    )
                    expected = single["signal"].iloc[-1]
                    if expected is None:
                        assert instrument_id not in vectorized.index
                    else:
                        assert vectorized[instrument_id] == expected

    def test_drops_instruments_with_no_signal(self):
        from app.services.crossover import scan_last_bar

        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        wide = pd.DataFrame({1: [50.0] * 10, 2: [10, 10, 10, 10, 10, 10, 10, 10, 30, 30]}, index=dates)
        result = scan_last_bar(wide, fast=2, slow=3, ma_type="sma")
        assert 1 not in result.index  # flat series, never crosses
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_crossover.py::TestScanLastBarParity -v`
Expected: FAIL with `ImportError: cannot import name 'scan_last_bar'`

- [ ] **Step 3: Add `scan_last_bar` to `app/services/crossover.py`**

```python
def scan_last_bar(wide: pd.DataFrame, fast: int, slow: int, ma_type: MaType) -> pd.Series:
    """Market-wide. `wide` is trade_date x instrument_id of adjusted_close.

    Every operation below runs across all instruments at once in pandas' C
    layer -- there is no Python-level loop over instruments anywhere in this
    function. Returns a Series indexed by instrument_id holding the signal on
    the final bar; instruments with no signal are dropped, not returned as None.
    """
    validate_periods(fast, slow)
    fast_ma = _moving_average(wide, fast, ma_type)
    slow_ma = _moving_average(wide, slow, ma_type)
    diff = fast_ma - slow_ma
    prev = diff.shift(1)

    above = (diff > 0) & (prev <= 0)
    below = (diff < 0) & (prev >= 0)

    out = pd.Series(index=wide.columns, dtype=object)
    out[above.iloc[-1]] = "crossed_above"
    out[below.iloc[-1]] = "crossed_below"
    return out.dropna()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crossover.py -v`
Expected: PASS, including `TestScanLastBarParity`

- [ ] **Step 5: Commit**

```bash
git add app/services/crossover.py tests/test_crossover.py
git commit -m "feat: add vectorized market-wide crossover scan with parity test"
```

---

### Task 3: DB loading, caching, and scan orchestration

**Files:**
- Create: `app/services/crossover_loader.py`
- Test: `tests/test_crossover_loader.py`

**Interfaces:**
- Consumes:
  - `warmup_bars`, `scan_last_bar`, `STALE_TOLERANCE_DAYS`, `MaType` from `app/services/crossover.py` (Tasks 1-2).
  - `app.db.session.engine` (existing).
  - `app.services.screening.latest_trade_date(db: Session) -> date | None` (existing, reused for the uncached freshness check).
- Produces:
  - `resolve_window(n_bars: int) -> tuple[date, date]` — `(cutoff, as_of)`.
  - `load_wide(cutoff: date) -> pd.DataFrame` — pivoted, forward-filled.
  - `run_scan(db: Session, fast: int, slow: int, ma_type: MaType, direction: Literal["crossed_above","crossed_below","any"]) -> ScanResult` — the function Task 5's API route calls directly. `ScanResult` fields: `as_of: date`, `matches: pd.Series` (instrument_id -> signal, already direction-filtered), `evaluated: int`, `skipped_insufficient_history: int`, `skipped_stale: int`, `elapsed_ms: int`, `cached: bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_crossover_loader.py
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
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 30, 30, 30], start)
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
        crossing_id = _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 30, 30, 30], start)

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
        _seed_instrument(db, "XYZ", [10, 10, 10, 10, 10, 10, 30, 30, 30], start)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crossover_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.crossover_loader'`

- [ ] **Step 3: Implement `app/services/crossover_loader.py`**

```python
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


@lru_cache(maxsize=32)
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
    n_bars = warmup_bars(slow, ma_type)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crossover_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/crossover_loader.py tests/test_crossover_loader.py
git commit -m "feat: add DB loading, caching, and orchestration for the market-wide crossover scan"
```

---

### Task 4: Pydantic schemas + instant single-instrument endpoint

**Files:**
- Create: `app/schemas/crossover.py`
- Create: `app/api/crossover.py`
- Modify: `app/main.py:1-43` (mount the new router)
- Modify: `tests/test_api.py` (add API test)

**Interfaces:**
- Consumes: `validate_periods`, `compute_crossover`, `MaType` from `app/services/crossover.py`; `load_price_history` from `app/jobs/compute_indicators.py` (existing); `get_current_user`, `get_db` from `app/api/deps.py` and `app/db/session.py` (existing).
- Produces:
  - `app.schemas.crossover.CrossoverPoint`, `CrossoverSeriesOut`, `ScanRequest`, `ScanStats`, `ScanMatchOut`, `ScanResponse` — consumed by Task 5's POST route and by the frontend.
  - `GET /api/instruments/{instrument_id}/crossover?fast=&slow=&ma_type=` — response model `CrossoverSeriesOut`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_api.py

class TestInstrumentCrossover:
    def test_returns_series_for_valid_periods(self, client, db, owner):
        from app.models import DailyPrice, Instrument
        from datetime import date, timedelta

        inst = Instrument(symbol="XOVR", exchange="NSE", company_name="Crossover Co", is_active=True)
        db.add(inst)
        db.flush()
        start = date(2026, 1, 1)
        for i, close in enumerate([10, 10, 10, 10, 10, 10, 30, 30, 30]):
            db.add(DailyPrice(
                instrument_id=inst.id, trade_date=start + timedelta(days=i),
                open=close, high=close, low=close, close=close, adjusted_close=close, volume=100,
            ))
        db.flush()

        resp = client.get(
            f"/api/instruments/{inst.id}/crossover",
            params={"fast": 2, "slow": 3, "ma_type": "sma"},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["instrument_id"] == inst.id
        assert any(p["signal"] == "crossed_above" for p in body["points"])

    def test_rejects_fast_greater_than_slow(self, client, db, owner):
        from app.models import DailyPrice, Instrument
        from datetime import date

        inst = Instrument(symbol="BAD", exchange="NSE", company_name="Bad Co", is_active=True)
        db.add(inst)
        db.flush()
        db.add(DailyPrice(instrument_id=inst.id, trade_date=date(2026, 1, 1), open=1, high=1, low=1, close=1, adjusted_close=1, volume=1))
        db.flush()

        resp = client.get(
            f"/api/instruments/{inst.id}/crossover",
            params={"fast": 50, "slow": 20, "ma_type": "sma"},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_404_for_unknown_instrument(self, client, owner):
        resp = client.get(
            "/api/instruments/999999/crossover",
            params={"fast": 9, "slow": 21, "ma_type": "ema"},
            headers=_auth(owner),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/instruments/1/crossover", params={"fast": 9, "slow": 21, "ma_type": "ema"})
        assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::TestInstrumentCrossover -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement `app/schemas/crossover.py`**

```python
"""Request/response shapes for the custom crossover indicator and scan
(app/api/crossover.py). Mirrors app/schemas/screen.py's pattern: validation
lives on the schema, reusing the same rules the compute layer enforces."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.crossover import validate_periods

MaType = Literal["sma", "ema"]
Signal = Literal["crossed_above", "crossed_below"]
Direction = Literal["crossed_above", "crossed_below", "any"]


class CrossoverPoint(BaseModel):
    trade_date: date
    fast: float | None
    slow: float | None
    signal: Signal | None


class CrossoverSeriesOut(BaseModel):
    instrument_id: int
    fast: int
    slow: int
    ma_type: MaType
    points: list[CrossoverPoint]


class ScanRequest(BaseModel):
    fast: int = Field(ge=1)
    slow: int = Field(ge=1)
    ma_type: MaType
    direction: Direction = "any"

    @model_validator(mode="after")
    def _valid_periods(self) -> "ScanRequest":
        validate_periods(self.fast, self.slow)
        return self


class ScanStats(BaseModel):
    evaluated: int
    matched: int
    skipped_insufficient_history: int
    skipped_stale: int
    elapsed_ms: int
    cached: bool


class ScanMatchOut(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    sector: str | None
    latest_close: float | None
    signal: Signal


class ScanResponse(BaseModel):
    as_of: date
    params: ScanRequest
    stats: ScanStats
    matches: list[ScanMatchOut]
```

- [ ] **Step 4: Implement `app/api/crossover.py` (GET route only for now)**

```python
"""Custom MA crossover: view on one instrument (instant) and scan the whole
market (Task 5, a few seconds). Additive to the existing indicator/screener
features -- see docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md.
"""

import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.jobs.compute_indicators import load_price_history
from app.models import Instrument
from app.schemas.crossover import CrossoverPoint, CrossoverSeriesOut, MaType
from app.services.crossover import compute_crossover, validate_periods

router = APIRouter(prefix="/api", tags=["crossover"], dependencies=[Depends(get_current_user)])


def _none_if_nan(v: float) -> float | None:
    return None if v is None or math.isnan(v) else float(v)


@router.get("/instruments/{instrument_id}/crossover", response_model=CrossoverSeriesOut)
def get_crossover(
    instrument_id: int,
    fast: int = Query(...),
    slow: int = Query(...),
    ma_type: MaType = Query(...),
    db: Session = Depends(get_db),
) -> CrossoverSeriesOut:
    if db.get(Instrument, instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")
    try:
        validate_periods(fast, slow)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    prices = load_price_history(db, instrument_id)
    if prices.empty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no price history for this instrument")

    result = compute_crossover(prices, fast, slow, ma_type)
    points = [
        CrossoverPoint(
            trade_date=trade_date,
            fast=_none_if_nan(row["fast"]),
            slow=_none_if_nan(row["slow"]),
            signal=row["signal"],
        )
        for trade_date, row in result.iterrows()
    ]
    return CrossoverSeriesOut(instrument_id=instrument_id, fast=fast, slow=slow, ma_type=ma_type, points=points)
```

- [ ] **Step 5: Mount the router in `app/main.py`**

```python
# app/main.py -- add alongside the other router imports (after instruments_router)
from app.api.crossover import router as crossover_router
```

```python
# app/main.py -- add alongside the other app.include_router calls (after instruments_router)
app.include_router(crossover_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_api.py::TestInstrumentCrossover tests/test_crossover.py tests/test_crossover_loader.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/schemas/crossover.py app/api/crossover.py app/main.py tests/test_api.py
git commit -m "feat: add instant single-instrument crossover endpoint"
```

---

### Task 5: Market-wide scan endpoint

**Files:**
- Modify: `app/api/crossover.py` (add POST route)
- Modify: `tests/test_api.py` (add API test)

**Interfaces:**
- Consumes: `run_scan` from `app/services/crossover_loader.py` (Task 3); `ScanRequest`, `ScanResponse`, `ScanStats`, `ScanMatchOut` from `app/schemas/crossover.py` (Task 4).
- Produces: `POST /api/scans/crossover` — response model `ScanResponse`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_api.py

class TestCrossoverScan:
    def test_finds_matches_across_instruments(self, client, db, owner):
        from app.models import DailyPrice, Instrument
        from datetime import date, timedelta

        crossing = Instrument(symbol="XOVR2", exchange="NSE", company_name="Crossing Co", sector="IT", is_active=True)
        flat = Instrument(symbol="FLAT2", exchange="NSE", company_name="Flat Co", is_active=True)
        db.add_all([crossing, flat])
        db.flush()

        start = date(2026, 1, 1)
        for i, close in enumerate([10, 10, 10, 10, 10, 10, 30, 30, 30]):
            db.add(DailyPrice(instrument_id=crossing.id, trade_date=start + timedelta(days=i), open=close, high=close, low=close, close=close, adjusted_close=close, volume=100))
        for i in range(9):
            db.add(DailyPrice(instrument_id=flat.id, trade_date=start + timedelta(days=i), open=50, high=50, low=50, close=50, adjusted_close=50, volume=100))
        db.flush()

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 3, "ma_type": "sma", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        symbols = {m["symbol"] for m in body["matches"]}
        assert "XOVR2" in symbols
        assert "FLAT2" not in symbols
        assert body["stats"]["matched"] == len(body["matches"])

    def test_rejects_invalid_periods(self, client, owner):
        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 50, "slow": 20, "ma_type": "sma", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.post("/api/scans/crossover", json={"fast": 9, "slow": 21, "ma_type": "ema", "direction": "any"})
        assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::TestCrossoverScan -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Add the POST route to `app/api/crossover.py`**

```python
# app/api/crossover.py -- add these imports alongside the existing ones
from app.models import DailyPrice, Instrument
from app.schemas.crossover import ScanMatchOut, ScanRequest, ScanResponse, ScanStats
from app.services.crossover_loader import run_scan
from sqlalchemy import select
```

```python
# app/api/crossover.py -- add below get_crossover

@router.post("/scans/crossover", response_model=ScanResponse)
def scan_crossover(payload: ScanRequest, db: Session = Depends(get_db)) -> ScanResponse:
    try:
        result = run_scan(db, payload.fast, payload.slow, payload.ma_type, payload.direction)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    instrument_ids = list(result.matches.index)
    rows = {}
    if instrument_ids:
        for inst, close in db.execute(
            select(Instrument, DailyPrice.adjusted_close)
            .join(DailyPrice, (DailyPrice.instrument_id == Instrument.id) & (DailyPrice.trade_date == result.as_of))
            .where(Instrument.id.in_(instrument_ids))
        ).all():
            rows[inst.id] = (inst, close)

    matches = [
        ScanMatchOut(
            instrument_id=instrument_id,
            symbol=rows[instrument_id][0].symbol,
            exchange=rows[instrument_id][0].exchange,
            sector=rows[instrument_id][0].sector,
            latest_close=float(rows[instrument_id][1]) if rows[instrument_id][1] is not None else None,
            signal=signal,
        )
        for instrument_id, signal in result.matches.items()
        if instrument_id in rows
    ]

    return ScanResponse(
        as_of=result.as_of,
        params=payload,
        stats=ScanStats(
            evaluated=result.evaluated,
            matched=len(matches),
            skipped_insufficient_history=result.skipped_insufficient_history,
            skipped_stale=result.skipped_stale,
            elapsed_ms=result.elapsed_ms,
            cached=result.cached,
        ),
        matches=matches,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api.py::TestCrossoverScan tests/test_api.py::TestInstrumentCrossover -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `pytest -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add app/api/crossover.py tests/test_api.py
git commit -m "feat: add market-wide crossover scan endpoint"
```

---

### Task 6: Frontend — custom crossover on the stock detail page

**Files:**
- Modify: `frontend/src/lib/types.ts` (add crossover types)
- Modify: `frontend/src/pages/StockDetailPage.tsx`

**Interfaces:**
- Consumes: `apiFetch` from `frontend/src/lib/api.ts` (existing); `GET /api/instruments/{id}/crossover` (Task 4).
- Produces: a `CustomCrossoverCard` component rendered inside `StockDetailPage.tsx`, reusable as-is by Task 7's scan results table for its signal styling.

- [ ] **Step 1: Add types to `frontend/src/lib/types.ts`**

```typescript
// Append to frontend/src/lib/types.ts -- mirrors app/schemas/crossover.py

export type MaType = 'sma' | 'ema'
export type CrossoverSignal = 'crossed_above' | 'crossed_below'
export type ScanDirection = CrossoverSignal | 'any'

export interface CrossoverPoint {
  trade_date: string
  fast: number | null
  slow: number | null
  signal: CrossoverSignal | null
}

export interface CrossoverSeriesOut {
  instrument_id: number
  fast: number
  slow: number
  ma_type: MaType
  points: CrossoverPoint[]
}

export interface ScanStats {
  evaluated: number
  matched: number
  skipped_insufficient_history: number
  skipped_stale: number
  elapsed_ms: number
  cached: boolean
}

export interface ScanMatchOut {
  instrument_id: number
  symbol: string
  exchange: string
  sector: string | null
  latest_close: number | null
  signal: CrossoverSignal
}

export interface ScanResponse {
  as_of: string
  params: { fast: number; slow: number; ma_type: MaType; direction: ScanDirection }
  stats: ScanStats
  matches: ScanMatchOut[]
}
```

- [ ] **Step 2: Add the crossover control to `StockDetailPage.tsx`**

```typescript
// frontend/src/pages/StockDetailPage.tsx -- add near the top, alongside the
// other imports
import { useState } from 'react'
import { apiFetch, ApiError } from '../lib/api'
import type { CrossoverSeriesOut } from '../lib/types'
```

```typescript
// frontend/src/pages/StockDetailPage.tsx -- new component, defined above
// StockDetailPage itself

function CustomCrossoverCard({ instrumentId }: { instrumentId: number }) {
  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [result, setResult] = useState<CrossoverSeriesOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fastNum = Number(fast)
  const slowNum = Number(slow)
  const invalid = !Number.isInteger(fastNum) || !Number.isInteger(slowNum) || fastNum < 1 || fastNum >= slowNum || slowNum > 400

  async function run() {
    if (invalid) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch<CrossoverSeriesOut>(
        `/api/instruments/${instrumentId}/crossover?fast=${fastNum}&slow=${slowNum}&ma_type=${maType}`,
      )
      setResult(res)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'failed to compute crossover')
    } finally {
      setLoading(false)
    }
  }

  const last = result?.points[result.points.length - 1] ?? null

  return (
    <div className="card blueprint" style={{ padding: 16 }}>
      <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
      <div className="card-kicker">Custom crossover</div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <input className="input" style={{ width: 64, fontSize: 13, padding: '5px 8px' }} value={fast} onChange={(e) => setFast(e.target.value)} placeholder="fast" />
        <span style={{ color: 'var(--color-neutral-500)' }}>/</span>
        <input className="input" style={{ width: 64, fontSize: 13, padding: '5px 8px' }} value={slow} onChange={(e) => setSlow(e.target.value)} placeholder="slow" />
        <select className="input" style={{ width: 84, fontSize: 13, padding: '5px 8px' }} value={maType} onChange={(e) => setMaType(e.target.value as 'sma' | 'ema')}>
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
        </select>
        <button type="button" className="btn btn-secondary" style={{ fontSize: 12.5, padding: '5px 12px' }} onClick={run} disabled={invalid || loading}>
          {loading ? 'Computing…' : 'Compute'}
        </button>
      </div>
      {invalid && <p className="text-muted" style={{ fontSize: 12 }}>fast must be a positive integer less than slow (max 400).</p>}
      {error && <p style={{ fontSize: 12, color: 'var(--color-neg-text)' }}>{error}</p>}
      {last && (
        <div style={{ fontSize: 13 }}>
          <div>Fast ({fastNum}): <strong>{last.fast?.toFixed(2) ?? '—'}</strong></div>
          <div>Slow ({slowNum}): <strong>{last.slow?.toFixed(2) ?? '—'}</strong></div>
          <div style={{ marginTop: 6 }}>
            {last.signal ? (
              <span className="tag tag-accent">{last.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'} as of {last.trade_date}</span>
            ) : (
              <span className="text-muted">No crossover on the latest bar</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Render it in the page**

```typescript
// frontend/src/pages/StockDetailPage.tsx -- inside the right-hand column div
// (the flex column that already holds the three IndicatorCard components),
// add after the "Volatility & range" IndicatorCard:

          <CustomCrossoverCard instrumentId={instrumentId} />
```

- [ ] **Step 4: Manual check**

Run the app (`uvicorn app.main:app --reload` and `npm run dev` per RUN.md), open a stock detail page, enter fast=9/slow=21/EMA, click Compute, confirm a result renders (either a signal tag or "No crossover"). Try fast=50/slow=20 and confirm the inline validation message appears without a network call.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/pages/StockDetailPage.tsx
git commit -m "feat: add custom crossover control to the stock detail page"
```

---

### Task 7: Frontend — Custom Scan page

**Files:**
- Create: `frontend/src/pages/CustomScanPage.tsx`
- Modify: `frontend/src/App.tsx` (add route)
- Modify: `frontend/src/components/AppShell.tsx` (add nav item)

**Interfaces:**
- Consumes: `apiFetch`, `ApiError` from `frontend/src/lib/api.ts`; `ScanResponse`, `ScanMatchOut` from `frontend/src/lib/types.ts` (Task 6); `usePageHeader` from `frontend/src/lib/pageHeader.tsx`; `fmtPrice` from `frontend/src/lib/format.tsx`; `POST /api/scans/crossover` (Task 5).
- Produces: route `/scan`, nav item "Custom Scan".

- [ ] **Step 1: Create `frontend/src/pages/CustomScanPage.tsx`**

```typescript
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, ApiError } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import type { ScanDirection, ScanResponse } from '../lib/types'

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Scan the whole market for a custom-period MA crossover — takes a few seconds, unlike the instant Screener')

  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [direction, setDirection] = useState<ScanDirection>('any')
  const [result, setResult] = useState<ScanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fastNum = Number(fast)
  const slowNum = Number(slow)
  const invalid = !Number.isInteger(fastNum) || !Number.isInteger(slowNum) || fastNum < 1 || fastNum >= slowNum || slowNum > 400

  async function runScan() {
    if (invalid || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch<ScanResponse>('/api/scans/crossover', {
        method: 'POST',
        body: JSON.stringify({ fast: fastNum, slow: slowNum, ma_type: maType, direction }),
      })
      setResult(res)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'scan failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <div className="field" style={{ margin: 0 }}>
          <label>Fast period</label>
          <input className="input" style={{ width: 90 }} value={fast} onChange={(e) => setFast(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Slow period</label>
          <input className="input" style={{ width: 90 }} value={slow} onChange={(e) => setSlow(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Type</label>
          <select className="input" style={{ width: 90 }} value={maType} onChange={(e) => setMaType(e.target.value as 'sma' | 'ema')}>
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Direction</label>
          <select className="input" style={{ width: 150 }} value={direction} onChange={(e) => setDirection(e.target.value as ScanDirection)}>
            <option value="any">Both</option>
            <option value="crossed_above">Crossed above</option>
            <option value="crossed_below">Crossed below</option>
          </select>
        </div>
        <button type="button" className="btn btn-primary blueprint" onClick={runScan} disabled={invalid || loading} style={{ whiteSpace: 'nowrap' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {loading ? 'Running…' : 'Run scan'}
        </button>
      </div>
      {invalid && <p className="text-muted" style={{ fontSize: 12, marginBottom: 14 }}>fast must be a positive integer less than slow (max 400).</p>}
      {error && <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 14 }}>{error}</p>}

      {result && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 12 }}>
            As of {result.as_of} — {result.stats.matched} of {result.stats.evaluated} evaluated
            {result.stats.skipped_stale > 0 && `, ${result.stats.skipped_stale} stale`}
            {result.stats.skipped_insufficient_history > 0 && `, ${result.stats.skipped_insufficient_history} short on history`}
            {' — '}{result.stats.elapsed_ms}ms{result.stats.cached ? ' (cached)' : ''}
          </p>
          {result.matches.length === 0 ? (
            <div style={{ padding: 26, textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13, border: '1px solid var(--color-neutral-300)' }}>
              No stocks currently match this crossover.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Symbol</th><th>Sector</th><th style={{ textAlign: 'right' }}>Price</th><th>Signal</th></tr>
              </thead>
              <tbody>
                {result.matches.map((m) => (
                  <tr key={m.instrument_id}>
                    <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.symbol}</strong></Link></td>
                    <td>{m.sector ? <span className="tag tag-outline">{m.sector}</span> : <span className="text-muted">—</span>}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.latest_close)}</td>
                    <td>
                      <span className="tag tag-accent">{m.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add the route in `frontend/src/App.tsx`**

```typescript
// frontend/src/App.tsx -- add import alongside the other page imports
import { CustomScanPage } from './pages/CustomScanPage'
```

```typescript
// frontend/src/App.tsx -- add inside the protected <Route> block, alongside
// the other page routes (e.g. after the /screener route)
                <Route path="/scan" element={<CustomScanPage />} />
```

- [ ] **Step 3: Add the nav item in `frontend/src/components/AppShell.tsx`**

```typescript
// frontend/src/components/AppShell.tsx -- add a new entry to NAV_ITEMS,
// after the '/screener' entry
  {
    to: '/scan',
    label: 'Custom Scan',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
    ),
  },
```

- [ ] **Step 4: Manual check**

With the backend and frontend both running, log in, click "Custom Scan" in the nav, run a scan with fast=9/slow=21/EMA/Both, confirm results render with the stats line and a working link to a stock's detail page. Try fast=20/slow=9 and confirm inline validation blocks the request.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CustomScanPage.tsx frontend/src/App.tsx frontend/src/components/AppShell.tsx
git commit -m "feat: add Custom Scan page for market-wide crossover scanning"
```

---

### Task 8: Benchmark against real data volume

**Files:**
- Create: `scripts/bench_crossover_scan.py`

**Interfaces:**
- Consumes: `run_scan` from `app/services/crossover_loader.py` (Task 3); `app.db.session.SessionLocal` (existing).
- Produces: a standalone report printed to stdout. Sub-project A is not considered done until this has been run once against the real, full-sized local database and its numbers recorded below.

- [ ] **Step 1: Write `scripts/bench_crossover_scan.py`**

```python
"""Standalone timing check for the market-wide crossover scan against real
data volume -- not a pytest test, a one-off report. The spec's "a few
seconds" promise to the user should be measured here, not assumed.

Run with: python -m scripts.bench_crossover_scan
Requires a locally loaded database with realistic history (see RUN.md's
backfill instructions) -- results against a handful of seeded rows in the
test suite don't tell you anything about the real ~7,500-instrument cost.
"""

import time

from app.db.session import SessionLocal
from app.services.crossover_loader import _load_wide_cached, _scan_cached, resolve_window, run_scan
from app.services.crossover import warmup_bars

SCENARIOS = [
    (9, 21, "ema"),
    (20, 50, "sma"),
    (50, 200, "sma"),
]


def main() -> None:
    db = SessionLocal()
    try:
        for fast, slow, ma_type in SCENARIOS:
            _scan_cached.cache_clear()
            _load_wide_cached.cache_clear()

            n_bars = warmup_bars(slow, ma_type)
            t0 = time.perf_counter()
            cutoff, as_of = resolve_window(n_bars)
            t1 = time.perf_counter()

            result = run_scan(db, fast, slow, ma_type, "any")
            t2 = time.perf_counter()

            result_cached = run_scan(db, fast, slow, ma_type, "any")
            t3 = time.perf_counter()

            print(f"\n{ma_type.upper()} {fast}/{slow} (warmup={n_bars} bars, window={cutoff}..{as_of}):")
            print(f"  resolve_window:        {(t1 - t0) * 1000:.0f}ms")
            print(f"  cold run (query+compute): {(t2 - t1) * 1000:.0f}ms")
            print(f"  warm run (cache hit):   {(t3 - t2) * 1000:.0f}ms")
            print(f"  evaluated={result.evaluated} matched={len(result.matches)} "
                  f"stale={result.skipped_stale} short_history={result.skipped_insufficient_history}")
            assert result_cached.cached is True
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the local database**

Run: `python -m scripts.bench_crossover_scan`
(Requires real backfilled data — see RUN.md's "Getting real data in" section. If the local DB only has a few days of history, back-fill at least a year first: `python -m app.jobs.backfill_prices --from 2025-01-01 --to 2026-08-01`.)

Expected: cold run well under the "a few seconds" target from the spec (design estimate: ~1-2s) for all three scenarios; warm run near-instant. If cold run is materially over target, see the spec's *Performance* section for the deferred index as the next step — do not silently accept a slower result without noting it.

- [ ] **Step 3: Record the measured numbers**

Append the actual output to the bottom of `docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md` under a new `## Measured performance (Task 8)` heading, so the "a few seconds" claim in the spec is backed by a real number instead of an estimate.

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_crossover_scan.py docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md
git commit -m "test: add and run market-wide crossover scan benchmark"
```
