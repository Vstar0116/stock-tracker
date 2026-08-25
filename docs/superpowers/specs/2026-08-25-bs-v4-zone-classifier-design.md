# BS-V4 Zone Classifier — Design

## Overview

A deterministic, rule-based classifier that buckets each tracked instrument into one of four technical-state zones (A–D) or "Unclassified"/"Insufficient Data", based on RSI, price vs. two moving averages, and proximity to two EMAs. Two read endpoints: single-instrument (instant) and full-universe scan. Stateless — no DB writes, no new tables, no schema changes.

**Scope note (see Naming below):** the original request used buy/sell-signal language ("Aggressive Dip Buy", "Profit Lock / Exit", "suggested limit/stop prices"). That conflicts with this project's explicit scope (`CLAUDE.md`: "No trading, no order placement, no investment advice"; "Buy/sell recommendations or signals presented as advice" is out of scope). This spec keeps every threshold and formula from the original request unchanged, but reframes all naming and output fields as neutral technical-state description, matching how the existing crossover-indicator feature phrases its output ("crossed above" / "crossed below", never "buy"/"sell").

## Goals

- Classify a stock's current technical state from RSI(14), price vs. 200 SMA, price vs. 9/21 EMA, ATR(14), and RVOL(20) — all parameters configurable, not hardcoded.
- Two endpoints: `GET /api/zone/{ticker}` (single, instant) and `GET /api/zone/scan` (full universe, vectorized, cached).
- Reuse the existing indicator calculation functions (`app/services/indicators.py`) rather than recomputing.
- No schema/migration changes, no new job/worker, no advice-shaped output.

## Non-goals

- No auto-execution, no order placement (explicitly out of scope per the original request too).
- No new persisted indicator columns — RSI/ATR periods must be overridable per call, which the persisted `indicators` table (fixed at RSI 14 / ATR 14 / EMA 20+50, no EMA 9/21 at all) can't support. Computed on the fly from raw price history each time, same pattern the crossover feature already established for the same reason.
- No live/intraday data — classification uses the same EOD `daily_prices` data every other feature in this app reads (architecture principle #1: never call an external API while a user waits).

## Naming — neutral technical-state labels

| Original | This spec | Trigger (math unchanged) |
|---|---|---|
| Zone A "Aggressive Dip Buy" | **Zone A — Pullback at Support** | RSI < `rsi_zone_a_max`, price > macro SMA, price within `near_ema_pct` of fast EMA or slow EMA |
| Zone B "Strategic Accumulation" | **Zone B — Mid-RSI Above Trend** | RSI within `rsi_zone_b_range`, price > macro SMA |
| Zone C "Tactical Hold" | **Zone C — Elevated RSI** | RSI within `rsi_zone_c_range` |
| Zone D "Profit Lock / Exit" | **Zone D — Overbought or Below Trend** | RSI ≥ `rsi_zone_d_min`, OR (price < macro SMA AND price < slow EMA) |

`suggested_limit_price` → **`atr_band_lower`** (Zone A only: `macro_sma - 0.5 * atr`)
`suggested_stop_floor` → **`atr_band_upper`** (Zone B only: `slow_ema + atr_limit_multiplier * atr`)

Both are described in the API schema and any UI copy purely as "ATR-derived reference levels" — descriptive numbers derived from volatility, never "suggested" or framed as an action. `reason` strings are factual and numeric only (e.g. `"RSI 42.5 < 55, price above 200 SMA, within 2% of 21 EMA"`) — no verbs like buy/sell/hold/exit/accumulate.

The `zone` field itself stays the short code (`"A"|"B"|"C"|"D"|"Unclassified"|"Insufficient Data"`) for programmatic use; the human-readable label ("Pullback at Support" etc.) is a separate `zone_label` field.

## Architecture

Three-layer split, mirroring the crossover-indicator feature's proven shape:

```
app/services/zone_classifier.py   pure calc: params, validation, single-row and wide classification
app/services/zone_loader.py       DB I/O + orchestration: load prices, compute indicators, cache, assemble results
app/api/zone.py                   the two endpoints
app/schemas/zone.py               request/response Pydantic models
```

### `zone_classifier.py` (pure, no I/O)

```python
@dataclass(frozen=True)
class ZoneParams:
    macro_sma_period: int = 200
    fast_ema_period: int = 9
    slow_ema_period: int = 21
    rsi_period: int = 14
    rsi_zone_a_max: float = 55
    rsi_zone_b_range: tuple[float, float] = (56, 65)
    rsi_zone_c_range: tuple[float, float] = (66, 71)
    rsi_zone_d_min: float = 72
    atr_period: int = 14
    atr_limit_multiplier: float = 0.25
    rvol_period: int = 20
    near_ema_pct: float = 0.02

    def __post_init__(self):
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("fast_ema_period must be < slow_ema_period")
        # + bounds checks: all periods >= 1, near_ema_pct > 0, ranges non-overlapping
        # and ordered (a_max <= b_range[0], b_range[1] < c_range[0], c_range[1] < d_min)

def classify_zone(rsi, price, macro_sma, fast_ema, slow_ema, params) -> tuple[str, str, str]:
    """Returns (zone_code, zone_label, reason). Priority order, first match wins:
    D -> C -> B -> A -> Unclassified. Pure scalar function, no pandas."""

def classify_zones_wide(rsi, price, macro_sma, fast_ema, slow_ema, params) -> pd.DataFrame:
    """Same rules, vectorized: all five inputs are same-shaped pd.Series indexed by
    instrument_id (one row = latest bar per instrument). Returns a DataFrame with
    zone_code/zone_label/reason columns, built via boolean masks in the same D->C->B->A
    priority order (later masks only apply to rows not yet classified)."""
```

`classify_zone` and `classify_zones_wide` share the same threshold logic (masks are literally the scalar conditions applied elementwise); a parity test asserts they agree on the same inputs, same pattern as the crossover feature's `_crossing_masks` parity test.

### `zone_loader.py` (DB I/O, orchestration, caching)

**Single-instrument path** (`get_zone_for_instrument`):
1. Look up `Instrument` by `id` → 404 if not found (same pattern as the crossover feature's single-instrument endpoint).
2. `load_price_history(db, instrument_id)` (existing function, reused as-is).
3. If fewer than `max(macro_sma_period, slow_ema_period, rsi_period, atr_period, rvol_period) + 1` rows of history → return `zone: "Insufficient Data"` (all numeric fields `null`), not an error.
4. Compute macro_sma/fast_ema/slow_ema via `sma()`/`ema()`, rsi via `rsi()`, atr via `atr()` (reusing the existing convention from `compute_indicators.py`: raw high/low/close + adjusted_close), volume_sma via `volume_sma()`, rvol = latest volume / latest volume_sma.
5. Take the last row of each computed series; if any required value is NaN → `zone: "Insufficient Data"`.
6. Call `classify_zone(...)`, assemble the response.

**Scan path** (`run_zone_scan`):
1. `latest_trade_date(db)` (existing function, reused) → `as_of`. 404 if `None` (no data at all).
2. One query loading the trailing `N = max(macro_sma_period, slow_ema_period, rsi_period, atr_period, rvol_period) + STALE_TOLERANCE_DAYS` bars of `(instrument_id, trade_date, high, low, close, adjusted_close, volume)` for all active instruments (mirrors the crossover feature's window-widening fix for forward-fill tolerance — reuse `STALE_TOLERANCE_DAYS`/the same widening logic if the crossover branch isn't merged yet, otherwise just the constant).
3. Pivot into five wide DataFrames (index=trade_date, columns=instrument_id): `high`, `low`, `close`, `adjusted_close`, `volume`. Forward-fill within tolerance, same as the crossover loader.
4. Compute macro_sma/fast_ema/slow_ema/rsi/atr/volume_sma **once each**, whole-DataFrame, via the (generalized — see below) existing functions.
5. Take the last row of each → five Series indexed by instrument_id, one rvol Series (`volume.iloc[-1] / volume_sma.iloc[-1]`).
6. Split instruments into three buckets:
   - **Insufficient history**: never had `N` bars of real data (distinct from stale).
   - **Skipped (NaN)**: had enough history but a required value is still NaN or missing after forward-fill (e.g. a genuine data gap) — listed separately in the response's `skipped` list, not silently dropped, not counted as insufficient-history.
   - **Classified**: everyone else → `classify_zones_wide(...)`.
7. Sort classified results: zone A first, then B, C, D, Unclassified, each ascending by RSI within the zone.
8. Cache the whole scan result behind an `lru_cache`-style TTL keyed on `(params, as_of)` — same shape as the crossover feature's fix-round conclusion: small `maxsize` (2–4), not the original spec's unexamined 32, since each cached frame can be tens of MB at this universe size.

### Generalizing `_wilder_smoothing` for whole-universe RSI/ATR

`app/services/indicators.py::_wilder_smoothing` currently hardcodes `result = pd.Series(float("nan"), index=values.index)`, which breaks if `values` is a DataFrame (columns=instruments) instead of a Series. The loop body (`avg.iloc[i]`-style arithmetic) already broadcasts correctly across either shape — only the container construction needs to stop assuming 1-D. Fix: build `result` shape-agnostically, e.g. `result = values * float("nan")` (preserves shape/index/columns for both Series and DataFrame, all-NaN), and replace the two `.iloc[...]` position assignments with forms that work for both shapes (`.iloc[window - 1] = avg` already works identically for Series and DataFrame row-assignment via a Series). `rsi()` and `atr()` themselves need no changes — they already just call `_wilder_smoothing` and do elementwise arithmetic on its result, which pandas already handles uniformly for Series or DataFrame.

This is additive/backward-compatible: every existing call site passes a Series (the nightly `compute_indicators.py` job, per-instrument), and Series-in/Series-out behavior is unchanged — verified by the existing indicator tests continuing to pass unmodified, plus new tests added for DataFrame input. This is the only production change outside the new `zone_*` files.

### `app/schemas/zone.py`

```python
class ZoneOut(BaseModel):
    ticker: str
    zone: Literal["A", "B", "C", "D", "Unclassified", "Insufficient Data"]
    zone_label: str
    rsi: float | None
    price: float | None
    macro_sma: float | None
    fast_ema: float | None
    slow_ema: float | None
    atr_band_lower: float | None   # Zone A only
    atr_band_upper: float | None   # Zone B only
    rvol: float | None
    reason: str

class ZoneScanResponse(BaseModel):
    as_of: date
    params: ZoneParamsOut            # echoes the effective (possibly overridden) params
    matches: list[ZoneOut]
    skipped: list[SkippedOut]        # ticker + reason (nan_data / insufficient_history)
    evaluated: int
    cached: bool
    elapsed_ms: int
```

### `app/api/zone.py`

```
GET /api/zone/{instrument_id}
  instrument_id: int. Query params: one per ZoneParams field, all optional
  (default = ZoneParams() defaults). 404 if instrument_id unknown. 422 (via
  ZoneParams validation) if fast_ema_period >= slow_ema_period or any range
  is malformed. Returns ZoneOut directly (single object, not wrapped).

GET /api/zone/scan
  Same query params. Returns ZoneScanResponse.
```

**Deviation from the original request's `GET /api/zone/{ticker}`:** routes by `instrument_id: int`, matching the existing `GET /api/instruments/{instrument_id}/crossover` convention this codebase already established for the crossover feature. A bare ticker string is ambiguous — `symbol` alone isn't unique (NSE and BSE can list the same text symbol), only `(symbol, exchange)` is — and the frontend already navigates everywhere by `instrument_id`. Resolves the ambiguity without adding a second disambiguating query param.

Same auth (`Depends(get_current_user)`), same DB dependency pattern as every other router in `app/api/`.

## Edge cases

- **Insufficient history** (new listing): `zone: "Insufficient Data"`, all numeric fields `null`, `reason` explains which window couldn't be filled. Never crash, never guess.
- **`fast_ema_period >= slow_ema_period`**: `ZoneParams.__post_init__` raises `ValueError` → FastAPI query-param validation surfaces this as a 422 with a clear message, not a 500 or silent nonsense output.
- **Missing/NaN data mid-scan**: instrument is excluded from `matches` and appended to `skipped` with a reason, `evaluated` count still includes it (matches the crossover feature's `evaluated = total_active` convention) — never silently dropped with no trace.
- **RSI exactly at a boundary** (55/56/65/66/71/72): boundaries are `<` at the low end and range-inclusive/`>=` at the high end per the original spec's stated operators — captured precisely in `classify_zone`'s conditionals and pinned by dedicated boundary tests.

## Performance

- Scan endpoint: one SQL query, five whole-DataFrame indicator computations (post `_wilder_smoothing` fix), no per-ticker Python loop — same vectorization shape that got the crossover scan into the few-seconds range for its two easier scenarios. Given this reuses the same warmup-window mechanics, expect a similar profile: fine at `macro_sma_period<=200`-ish windows, worth a real benchmark (not just an estimate) before calling it done, same lesson learned from the crossover feature's Task 8.
- Cache: `lru_cache`-based, small `maxsize` (2–4), keyed on the full param tuple + `as_of` — not a literal 60-second wall-clock TTL. Decision: since this app is EOD-only (architecture principle #1 — no live data), `as_of` changes at most once per day, so a cache keyed on it already gives "free repeat scans until the data actually changes," which is strictly better than a 60s window for this data's actual update cadence. This matches the precedent already set by the crossover feature's scan cache. A literal 60s decay independent of `as_of` would mean re-computing every 60 seconds even though the underlying EOD data hasn't moved — not useful here.

## Testing

- `zone_classifier.py`: boundary tests at RSI 55/56/65/66/71/72 (inclusive/exclusive per spec), macro-filter override test (Zone D fires even when RSI alone would suggest Zone A, if price is below both macro SMA and slow EMA), `Unclassified` fallthrough test (a combination matching no rule), `ZoneParams` validation test (`fast >= slow` raises), parity test between `classify_zone` and `classify_zones_wide` on the same inputs.
- `zone_loader.py`: insufficient-history test, NaN/skipped-instrument test (present in `skipped`, absent from `matches`, still counted in `evaluated`), cache-hit test (repeat call with identical params is a cache hit), `_wilder_smoothing` DataFrame-vs-Series-equivalence test (computing RSI/ATR one instrument at a time vs. all at once in a wide frame gives the same numbers).
- `indicators.py`: existing Series-based tests must pass unmodified; add DataFrame-input tests for `_wilder_smoothing`/`rsi`/`atr`.
- `api/zone.py`: single-ticker happy path, 404 unknown ticker, 422 invalid params, scan happy path (sort order: zone A→D, RSI ascending within zone), scan with a mixed matches/skipped universe.
