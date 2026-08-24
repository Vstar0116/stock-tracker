# Custom MA Crossover Indicator + Custom Scan — Design Spec (v2)

Revision of the original sub-project A spec. Changes from v1 are confined to
sub-project A: ambiguous behavior is now pinned down, and the market-wide scan
is redesigned around a single query over a trimmed window plus vectorized
computation. Sub-projects B, C, D are unchanged and remain outline-only.

## Context

The client asked for a finance app "similar to TradingView Subscribed Version,"
for his own personal use only (not a public product). Personal-use framing
means the existing CLAUDE.md constraints stay in force: no billing, no
multi-tenancy, no public signup, no social features, no order placement or
investment advice. He specifically wants these capabilities built into this
app, not a separate TradingView subscription.

The full ask decomposes into four dependency-ordered sub-projects:

- **A. Custom, user-configurable moving-average crossover indicator** (this spec)
- **B. Replace the embedded read-only TradingView chart** with an interactive
  charting component (TradingView's own open-source "Lightweight Charts"
  library) so custom-computed data can actually be drawn on a chart.
- **C. Backtesting engine** — define entry/exit rules, simulate against stored
  history, report performance.
- **D. Backtest results UI** — equity curve + trade list, built on top of B.

Build order stays A → B → C → D.

---

## Sub-project A: Custom MA Crossover Indicator

### Goal

Let the user specify any fast/slow period pair and MA type (SMA or EMA), and:

1. View the crossover series for one chosen stock, computed instantly from full
   stored history.
2. Run a scan across the whole market for stocks whose most recent trading day
   shows a crossover, computed on demand — a separate feature from the existing
   instant Screener, not a replacement for it.

### Non-goals

- No changes to the `indicators` table schema or the nightly
  `compute_indicators` job — computed on demand from stored price history,
  never precomputed or stored.
- No changes to the Screener / `ScreenRule` DSL — additive, separate route.
- No job queue, no async workers, no polling, no progress bar.
- No advice or signal language in the UI — results are historical state
  ("crossed above as of `trade_date` X"), never a recommendation.
- **No new DB migration in v1.** One optional index is identified in
  *Performance* below as the single escape hatch if measured timings miss
  target; it is explicitly deferred, not included.

---

### Semantics — decisions that were previously ambiguous

These are the gaps v1 left open. Each is now a fixed contract that the code and
tests enforce.

**Crossover definition.** With `diff = fast_ma - slow_ma`:

- `crossed_above` at bar *t* iff `diff[t] > 0` **and** `diff[t-1] <= 0`
- `crossed_below` at bar *t* iff `diff[t] < 0` **and** `diff[t-1] >= 0`
- otherwise `None`

A bar where the two lines are exactly equal counts as *not yet crossed*; the
signal fires on the first bar that strictly clears zero. This makes the two
directions mutually exclusive and prevents a flat-equal stretch from emitting a
signal on every bar.

**Direction is optional.** The scan accepts
`direction: "crossed_above" | "crossed_below" | "any"`, defaulting to `"any"`.
Every match carries its own `signal` field, so one run answers both questions
instead of forcing two round trips.

**"Most recent trading day" means the market's latest, not the instrument's.**
`as_of` is the maximum `trade_date` present in the price table. An instrument
whose own last bar predates `as_of` by more than `STALE_TOLERANCE_DAYS` (5
trading days, configurable) is excluded and counted as stale rather than
silently evaluated against an old bar. Short gaps within tolerance are
forward-filled, the standard convention for halts and non-trading days.

**Both MAs use the same `ma_type`.** Mixed SMA/EMA pairs are not supported.

**Validation.** `fast` and `slow` must be integers with
`1 <= fast < slow <= 400`. Anything else is a 422 with a message naming the
offending field. The 400 ceiling is what makes the warm-up window bounded and
therefore the scan's cost predictable.

**Insufficient history.** The existing NaN-until-enough-history convention
holds: no partial-window values are ever emitted. Instruments without enough
bars to produce two consecutive `slow` values are excluded and counted, not
returned as non-matches.

**Adjusted close only.** Same rule as every other indicator in this app —
`adjusted_close`, never raw `close`. A consequence worth stating: because
adjusted history is rewritten by corporate actions, a scan re-run after a split
can legitimately return different historical results. This is expected, not a
bug.

---

### Architecture

#### New pure calculation module: `app/services/crossover.py`

Two entry points — one per-instrument, one vectorized across the market — that
must agree bar-for-bar. A parity test enforces this (see *Testing*).

```python
from typing import Literal, Optional
import pandas as pd
from app.services.indicators import sma, ema

MaType = Literal["sma", "ema"]
Signal = Literal["crossed_above", "crossed_below"]

MAX_PERIOD = 400
STALE_TOLERANCE_DAYS = 5


def validate_periods(fast: int, slow: int) -> None:
    """Raise ValueError on any invalid period pair. Callers map to HTTP 422."""
    if fast < 1 or slow < 1:
        raise ValueError("fast and slow must be positive integers")
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be less than slow ({slow})")
    if slow > MAX_PERIOD:
        raise ValueError(f"slow must not exceed {MAX_PERIOD}")


def warmup_bars(slow: int, ma_type: MaType) -> int:
    """Minimum trailing bars needed for the last two MA values to be sound.

    SMA is a finite window: slow + 1 bars give exactly two consecutive values.
    EMA is recursive, so its value depends on the whole series through a
    decaying weight. Truncating at k bars leaves a seed error of roughly
    (1 - 2/(slow+1))^k. At k = 6 * slow that is under 1e-5 relative -- far
    below any price resolution that could flip a crossover. The 250 floor
    keeps short windows generously warmed.
    """
    return slow + 1 if ma_type == "sma" else max(250, 6 * slow)


def _ma(series_or_frame, window: int, ma_type: MaType):
    """MA over a Series or a wide DataFrame. Must mirror indicators.sma/ema
    exactly -- same min_periods and same adjust flag."""
    if ma_type == "sma":
        return series_or_frame.rolling(window=window, min_periods=window).mean()
    return series_or_frame.ewm(span=window, adjust=False, min_periods=window).mean()


def compute_crossover(
    prices: pd.DataFrame, fast: int, slow: int, ma_type: MaType
) -> pd.DataFrame:
    """Single instrument, full series. Indexed by trade_date, columns
    fast / slow / signal. Used by the instant endpoint and by charting in B."""
    validate_periods(fast, slow)
    close = prices["adjusted_close"]
    fast_ma, slow_ma = _ma(close, fast, ma_type), _ma(close, slow, ma_type)
    diff = fast_ma - slow_ma
    prev = diff.shift(1)

    signal = pd.Series(None, index=prices.index, dtype=object)
    signal[(diff > 0) & (prev <= 0)] = "crossed_above"
    signal[(diff < 0) & (prev >= 0)] = "crossed_below"
    return pd.DataFrame({"fast": fast_ma, "slow": slow_ma, "signal": signal})


def scan_last_bar(
    wide: pd.DataFrame, fast: int, slow: int, ma_type: MaType
) -> pd.Series:
    """Market-wide. `wide` is trade_date x instrument_id of adjusted_close.

    Every operation below runs across all instruments at once in pandas' C
    layer -- there is no Python-level loop over instruments anywhere in the
    hot path. Returns a Series indexed by instrument_id holding the signal
    on the final bar; instruments with no signal are dropped.
    """
    validate_periods(fast, slow)
    fast_ma, slow_ma = _ma(wide, fast, ma_type), _ma(wide, slow, ma_type)
    diff = fast_ma - slow_ma
    prev = diff.shift(1)

    above = ((diff > 0) & (prev <= 0)).iloc[-1]
    below = ((diff < 0) & (prev >= 0)).iloc[-1]

    out = pd.Series(index=wide.columns, dtype=object)
    out[above] = "crossed_above"
    out[below] = "crossed_below"
    return out.dropna()
```

#### Price loading for the scan: `app/services/crossover_loader.py`

The efficiency of the whole feature lives here. Two ideas do the work:

**1. Load only the tail you need, not full history.** The scan reads exactly one
bar's signal, so it needs `warmup_bars(slow, ma_type)` trailing bars -- not the
~1,250+ bars of full history each instrument holds. For a 21-period EMA that is
250 bars instead of 1,250: roughly a **5x reduction in rows**, and far more for
instruments with long histories.

**2. One query for the entire market, not one per instrument.** A per-instrument
loop is an N+1 pattern -- 7,500 round trips whose latency alone dominates
everything else. Instead, resolve a cutoff date once, then range-scan.

```python
def resolve_window(conn, n_bars: int) -> tuple[date, date]:
    """One cheap query for the market calendar. All instruments share a
    trading calendar, so the Nth-most-recent distinct trade_date is a valid
    cutoff for every instrument at once."""
    rows = conn.execute("""
        SELECT trade_date FROM (
            SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC
            LIMIT :n
        ) t ORDER BY trade_date ASC
    """, {"n": n_bars}).fetchall()
    return rows[0][0], rows[-1][0]   # (cutoff, as_of)


def load_wide(conn, cutoff: date) -> pd.DataFrame:
    """One range scan, pivoted to trade_date x instrument_id."""
    long = pd.read_sql("""
        SELECT p.instrument_id, p.trade_date, p.adjusted_close
        FROM daily_prices p
        JOIN instruments i ON i.id = p.instrument_id
        WHERE i.is_active AND p.trade_date >= :cutoff
    """, conn, params={"cutoff": cutoff})

    wide = long.pivot(index="trade_date", columns="instrument_id",
                      values="adjusted_close").sort_index()
    return wide.ffill(limit=STALE_TOLERANCE_DAYS)
```

For a 21/EMA scan the loaded set is roughly 7,500 x 250 -- about 1.9M rows,
pivoting to a dense frame of about 15 MB -- comfortable in memory, and the
subsequent `rolling`/`ewm` calls cover the whole market in a few tens of
milliseconds.

Instruments still NaN on the last row after the bounded `ffill` are stale;
instruments NaN in the MA output are short on history. Both are counted and
excluded rather than reported as non-matches.

#### Caching

The nightly job is the only thing that changes prices, so a result is valid
until `as_of` advances. Putting `as_of` in the cache key makes invalidation
automatic -- no TTL, no manual bust:

```python
@lru_cache(maxsize=64)
def _scan_cached(fast: int, slow: int, ma_type: MaType, as_of: date) -> tuple:
    ...
```

Re-running the same parameters -- the common case when the user is eyeballing
results -- returns in microseconds. A second small cache on the loaded wide
frame, keyed by `(n_bars, as_of)`, means varying only `fast` (which does not
change `warmup_bars`) skips the query entirely.

#### API endpoints: `app/api/crossover.py`

New router, mounted in `app/main.py`, same JWT auth as every other route
(`Depends(get_current_user)`).

**`GET /api/instruments/{instrument_id}/crossover`**
Query: `fast`, `slow`, `ma_type`, optional `from` / `to` to trim the payload.
Loads full history via the existing `load_price_history` pattern, runs
`compute_crossover`, returns the full series for chart plotting in B. Instant --
one instrument, a few thousand rows.

**`POST /api/scans/crossover`**
Body: `{fast, slow, ma_type, direction}` with `direction` defaulting to `"any"`.

```jsonc
{
  "as_of": "2026-08-21",
  "params": { "fast": 9, "slow": 21, "ma_type": "ema", "direction": "any" },
  "stats": {
    "evaluated": 7412,
    "matched": 63,
    "skipped_insufficient_history": 71,
    "skipped_stale": 17,
    "elapsed_ms": 1180,
    "cached": false
  },
  "matches": [
    {
      "instrument_id": 1042, "symbol": "...", "exchange": "...",
      "sector": "...", "latest_close": 1234.5,
      "signal": "crossed_above"
    }
  ]
}
```

`matches[]` keeps `ScreenMatchOut`'s shape plus `signal`, so the results table
reuses the Screener's row rendering. The `stats` block makes exclusions visible
instead of leaving the user to wonder why a symbol is missing.

---

### Performance

| | v1 design | v2 design |
|---|---|---|
| DB round trips | ~7,500 | 2 |
| Rows read | ~9.4M (full history) | ~1.9M (trimmed window) |
| Per-instrument Python loop | yes | no |
| Repeat run, same params | full recompute | cache hit |
| Expected wall clock | tens of seconds | ~1-2s cold, instant warm |

Cost is now dominated by the single range scan rather than by round-trip
latency or interpreter overhead.

**The one deferred optimization.** If measured cold-run time misses target, the
fix is a covering index on `daily_prices (trade_date, instrument_id)` including
`adjusted_close`, letting the range scan serve from the index alone. This is a
migration, so it stays out of v1 per the non-goals -- but it is the first thing
to reach for, and it is worth confirming the existing
`ix_daily_prices_instrument_trade_date` index shape before assuming a new one
is needed.

**Concurrency.** Single user, so the synchronous request is fine. The wide
frame is the only real memory spike; with `slow` capped at 400 the worst case
(400 x 6 = 2,400 bars x 7,500 instruments -- about 140 MB) is the number to
sanity-check on the target box before shipping. If that is tight, cap `slow`
lower -- the ceiling exists precisely so this is bounded.

---

### Frontend

**Stock detail page** (`StockDetailPage.tsx`): a "Custom crossover" control --
two number inputs, an SMA/EMA toggle, calling the instant endpoint. Until B
lands the result renders as a compact text summary (fast value, slow value,
current signal, `as_of` date); plotting is B's job. Client-side validation
mirrors the server's rules so `fast >= slow` is caught before a request goes
out.

**New page: Custom Scan** (`/scan`, new nav item): the same three inputs plus a
direction toggle with an explicit **Both** option, a "Run scan" button, and a
results table reusing `ScreenerPage.tsx`'s row patterns with a Signal column
added. The button disables while in flight and shows elapsed time; the `stats`
line renders above the table. Kept a separate page from the Screener -- not a
tab -- so its on-demand behavior is never confused with the Screener's instant
live preview.

---

### Testing

`tests/test_crossover.py`, mirroring `tests/test_indicators.py`'s style:

- Clean crossover in each direction on a hand-built series with known turn points.
- No crossover -- flat and monotonic series both yield all-`None`.
- Insufficient history yields NaN, never a partial-window value.
- `fast >= slow`, non-positive, and over-ceiling periods all rejected.
- **Exact-equality bar**: `fast_ma == slow_ma` emits no signal, and the
  following bar that strictly clears zero does.
- **Parity**: for a set of random series, `scan_last_bar` on the pivoted frame
  equals `compute_crossover(...).signal.iloc[-1]` per instrument. This is the
  test that keeps the two endpoints from disagreeing.
- **EMA truncation**: EMA over `warmup_bars` trailing bars matches EMA over
  full history on the last bar to within tolerance -- the empirical check on
  the argument in `warmup_bars`' docstring.
- Stale instrument excluded and counted; short gap inside tolerance
  forward-filled and evaluated.

API tests in `tests/test_api.py`'s style: `POST /api/scans/crossover` against a
small seeded universe, covering each `direction` value, the `stats` counts, and
cache behavior (second identical call is a hit; a call after `as_of` advances
is a miss).

`scripts/bench_crossover_scan.py`: a standalone timing run against the full
~7,500-instrument universe, reporting query time, pivot time, compute time, and
total. **Sub-project A is not done until this has been run against real data
volume** -- "a few seconds" is a commitment the spec makes to the user, and it
should be measured rather than assumed.

---

## Sub-projects B, C, D — outline only

### B. Interactive charting component

Replace `TradingViewChart.tsx`'s read-only embed with TradingView's
open-source "Lightweight Charts" library (client-side, free, MIT-licensed)
so custom-computed series -- crossover lines now, backtest trade markers
later -- can be drawn directly. Candle data already exists via
`/api/instruments/{id}/prices`; this is a new rendering layer, not new data.

### C. Backtesting engine

Given entry/exit rules -- likely reusing the `ScreenRule` DSL from
`app/schemas/screen.py`, or a purpose-built entry/exit pair -- simulate trades
over an instrument's stored history and report total return, win rate, max
drawdown, and a trade-by-trade log. Needs a new service module and likely new
tables for runs and results. Framed strictly as historical simulation ("if
this rule had been followed"), never as forward-looking advice.

### D. Backtest results UI

Results page on top of B: equity curve, trade markers overlaid on the price
chart, trade log table, summary stats.

---

This spec covers sub-project A in full. B, C, and D get their own
spec-and-plan cycle when we reach them.
