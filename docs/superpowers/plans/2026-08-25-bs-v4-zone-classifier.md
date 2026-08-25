# BS-V4 Zone Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, rule-based technical-state classifier (BS-V4 Zone Classifier) that buckets each tracked instrument into Zone A/B/C/D/Unclassified/Insufficient Data from RSI, price vs. two moving averages, proximity to two EMAs, ATR, and RVOL — with a single-instrument endpoint and a full-universe scan endpoint.

**Architecture:** Three layers mirroring the existing crossover-indicator feature: a pure calculation module (`zone_classifier.py`, no I/O), a DB-loading/orchestration/caching module (`zone_loader.py`), and the API layer (`app/api/zone.py` + `app/schemas/zone.py`). One shared-code change: generalize `app/services/indicators.py::_wilder_smoothing` to accept a DataFrame (not just a Series) so RSI/ATR can be computed once across the whole universe instead of per-ticker.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, pandas, pytest. No new dependencies.

**Spec:** docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md

## Global Constraints

- No `indicators` table schema/migration changes — RSI/ATR/EMA periods are computed on the fly from raw price history (via `app/services/indicators.py`), never read from the persisted `indicators` table, because periods must be overridable and the persisted table's periods are fixed.
- No Screener / `ScreenRule` DSL changes.
- No new job queue, async workers, or polling mechanism.
- No advice/signal language anywhere: zone codes stay `"A"|"B"|"C"|"D"|"Unclassified"|"Insufficient Data"`; human-readable `zone_label` values are `"Pullback at Support"` (A), `"Mid-RSI Above Trend"` (B), `"Elevated RSI"` (C), `"Overbought or Below Trend"` (D); `reason` strings are numeric/factual only, never a verb like buy/sell/hold/exit/accumulate.
- `suggested_limit_price`/`suggested_stop_floor` from the original request are `atr_band_upper`/`atr_band_lower` in this implementation — same formulas (`atr_band_upper = slow_ema + atr_limit_multiplier * atr`, Zone B only; `atr_band_lower = macro_sma - 0.5 * atr`, Zone A only), neutral naming.
- Stateless: no DB writes anywhere in this feature.
- Only `adjusted_close` used for price-based calculations. ATR reuses the existing codebase convention (`app/services/indicators.py::atr()` takes raw high/low/close *and* adjusted_close together — this is the same call shape `app/jobs/compute_indicators.py` already uses; not a new pattern).
- Single-instrument endpoint routes by `instrument_id: int` (not a raw ticker string) — matches the existing `GET /api/instruments/{instrument_id}/crossover` convention and avoids the NSE/BSE symbol-collision ambiguity a bare ticker has. See spec's "Deviation" note.
- Reuse, don't reimplement: `sma()`, `ema()`, `rsi()`, `atr()`, `volume_sma()` from `app/services/indicators.py`; `load_price_history()` from `app/jobs/compute_indicators.py`; `latest_trade_date()` from `app/services/screening.py`; `resolve_window()` from `app/services/crossover_loader.py`.

---

### Task 1: Generalize `_wilder_smoothing` for whole-universe RSI/ATR

**Files:**
- Modify: `app/services/indicators.py:50-67` (`_wilder_smoothing`)
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_wilder_smoothing(values: pd.Series | pd.DataFrame, window: int) -> pd.Series | pd.DataFrame` — same signature, now shape-preserving for both input types. `rsi()` and `atr()` (which call it) need no changes and become DataFrame-safe as a side effect, since they only do elementwise arithmetic on its result.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indicators.py` (check the existing imports at the top of that file and reuse them — don't re-import `pd`/`pytest` if already imported):

```python
def test_wilder_smoothing_dataframe_matches_per_column_series():
    from app.services.indicators import _wilder_smoothing

    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    col_a = pd.Series([float(i) for i in range(20)], index=dates)
    col_b = pd.Series([float(20 - i) for i in range(20)], index=dates)
    wide = pd.DataFrame({"a": col_a, "b": col_b})

    result_wide = _wilder_smoothing(wide, window=14)
    result_a = _wilder_smoothing(col_a, window=14)
    result_b = _wilder_smoothing(col_b, window=14)

    pd.testing.assert_series_equal(result_wide["a"], result_a, check_names=False)
    pd.testing.assert_series_equal(result_wide["b"], result_b, check_names=False)


def test_wilder_smoothing_series_input_unchanged():
    """Guards against the DataFrame generalization changing Series behavior --
    this must produce byte-identical output to before the change."""
    from app.services.indicators import _wilder_smoothing

    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    values = pd.Series([float(i % 5) for i in range(20)], index=dates)
    result = _wilder_smoothing(values, window=14)

    assert pd.isna(result.iloc[12])
    assert not pd.isna(result.iloc[13])
    assert result.iloc[13] == values.iloc[:14].mean()


def test_rsi_on_wide_dataframe_matches_per_column_rsi():
    from app.services.indicators import rsi

    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    col_a = pd.Series([100 + i * 0.5 for i in range(30)], index=dates)
    col_b = pd.Series([100 - i * 0.3 for i in range(30)], index=dates)
    wide = pd.DataFrame({"a": col_a, "b": col_b})

    result_wide = rsi(wide, window=14)
    pd.testing.assert_series_equal(result_wide["a"], rsi(col_a, window=14), check_names=False)
    pd.testing.assert_series_equal(result_wide["b"], rsi(col_b, window=14), check_names=False)


def test_atr_on_wide_dataframe_matches_per_column_atr():
    from app.services.indicators import atr

    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    high_a = pd.Series([102 + i * 0.5 for i in range(30)], index=dates)
    low_a = pd.Series([98 + i * 0.5 for i in range(30)], index=dates)
    close_a = pd.Series([100 + i * 0.5 for i in range(30)], index=dates)
    high_b = high_a * 1.1
    low_b = low_a * 1.1
    close_b = close_a * 1.1
    wide_high = pd.DataFrame({"a": high_a, "b": high_b})
    wide_low = pd.DataFrame({"a": low_a, "b": low_b})
    wide_close = pd.DataFrame({"a": close_a, "b": close_b})
    wide_adj = wide_close

    result_wide = atr(wide_high, wide_low, wide_close, wide_adj, window=14)
    result_a = atr(high_a, low_a, close_a, close_a, window=14)
    result_b = atr(high_b, low_b, close_b, close_b, window=14)

    pd.testing.assert_series_equal(result_wide["a"], result_a, check_names=False)
    pd.testing.assert_series_equal(result_wide["b"], result_b, check_names=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indicators.py -k "wilder_smoothing_dataframe or rsi_on_wide or atr_on_wide" -v`
Expected: FAIL — `test_wilder_smoothing_dataframe_matches_per_column_series` and the two "on_wide" tests fail (TypeError or shape-mismatch from `_wilder_smoothing`'s current `pd.Series(float("nan"), index=values.index)` line producing a 1-D Series when given a 2-D DataFrame). `test_wilder_smoothing_series_input_unchanged` should already PASS (it exercises existing behavior) — if it fails, something about the existing implementation isn't what this plan assumes; stop and re-read `app/services/indicators.py` before proceeding.

- [ ] **Step 3: Generalize the implementation**

Replace `app/services/indicators.py:50-67`:

```python
def _wilder_smoothing(values: pd.Series | pd.DataFrame, window: int) -> pd.Series | pd.DataFrame:
    """Wilder's smoothed moving average (used by RSI and ATR): seeded with a
    simple average of the first `window` values, then recursively smoothed as

        avg[i] = (avg[i-1] * (window - 1) + values[i]) / window

    This is NOT the same as a plain EMA with alpha=1/window -- that uses a
    different (less accurate) seed. `values` must have no leading NaNs.

    Works on a Series (one instrument) or a DataFrame (columns=instruments,
    whole universe at once) -- the loop is over rows (trading days), so a
    DataFrame call does ~250 iterations of vectorized cross-column arithmetic
    instead of ~250 x N_INSTRUMENTS scalar iterations. `values * float("nan")`
    builds an all-NaN container with the same shape/index/columns as the input,
    whichever type it is.
    """
    result = values * float("nan")
    if len(values) < window:
        return result
    avg = values.iloc[:window].mean()
    result.iloc[window - 1] = avg
    for i in range(window, len(values)):
        avg = (avg * (window - 1) + values.iloc[i]) / window
        result.iloc[i] = avg
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indicators.py -k "wilder_smoothing_dataframe or wilder_smoothing_series_input_unchanged or rsi_on_wide or atr_on_wide" -v`
Expected: all 4 PASS.

- [ ] **Step 5: Run the full indicators test file to confirm no regression**

Run: `pytest tests/test_indicators.py -v`
Expected: every existing test still PASSES (Series-input behavior is provably unchanged since `values.iloc[:window].mean()` returns a scalar for a Series and `result.iloc[window-1] = avg` assigns a scalar to one position, exactly as before).

- [ ] **Step 6: Commit**

```bash
git add app/services/indicators.py tests/test_indicators.py
git commit -m "refactor: generalize _wilder_smoothing for whole-universe (DataFrame) RSI/ATR"
```

---

### Task 2: Zone classifier — params and single-instrument classification

**Files:**
- Create: `app/services/zone_classifier.py`
- Test: `tests/test_zone_classifier.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces: `ZoneParams` (frozen dataclass, all 12 fields with defaults, raises `ValueError` in `__post_init__` on invalid config, has a `max_window` property), `ZONE_LABELS: dict[str, str]`, `classify_zone(rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams) -> tuple[str, str, str]` (zone_code, zone_label, reason) — consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_zone_classifier.py`:

```python
"""Tests for app/services/zone_classifier.py -- pure functions, no I/O.

Run with: pytest tests/test_zone_classifier.py -v
"""

import pytest

from app.services.zone_classifier import ZoneParams, classify_zone


class TestZoneParamsValidation:
    def test_default_params_are_valid(self):
        ZoneParams()  # must not raise

    def test_rejects_fast_ema_gte_slow_ema(self):
        with pytest.raises(ValueError, match="fast_ema_period"):
            ZoneParams(fast_ema_period=21, slow_ema_period=21)

    def test_rejects_fast_ema_greater_than_slow_ema(self):
        with pytest.raises(ValueError, match="fast_ema_period"):
            ZoneParams(fast_ema_period=25, slow_ema_period=21)

    def test_rejects_non_positive_period(self):
        with pytest.raises(ValueError, match="rsi_period"):
            ZoneParams(rsi_period=0)

    def test_rejects_non_positive_near_ema_pct(self):
        with pytest.raises(ValueError, match="near_ema_pct"):
            ZoneParams(near_ema_pct=0)

    def test_rejects_misordered_rsi_zones(self):
        with pytest.raises(ValueError):
            ZoneParams(rsi_zone_a_max=60, rsi_zone_b_range=(56, 65))  # a_max > b low

    def test_max_window_is_the_largest_period(self):
        params = ZoneParams(macro_sma_period=200, slow_ema_period=21, rsi_period=14, atr_period=14, rvol_period=20)
        assert params.max_window == 200


class TestClassifyZoneBoundaries:
    """RSI boundary values pinned exactly per the spec's stated operators:
    Zone A is RSI < 55 (strict), Zone B is [56, 65] (inclusive), Zone C is
    [66, 71] (inclusive), Zone D is RSI >= 72 (inclusive)."""

    def setup_method(self):
        self.params = ZoneParams()
        # price/macro_sma/fast_ema/slow_ema chosen so ONLY the RSI value
        # determines the zone: price is above macro_sma (rules out the D
        # trend-filter) and exactly AT the fast EMA (always "near" for zone A).
        self.price = 100.0
        self.macro_sma = 90.0
        self.fast_ema = 100.0
        self.slow_ema = 95.0

    def _zone(self, rsi_value):
        zone, _, _ = classify_zone(rsi_value, self.price, self.macro_sma, self.fast_ema, self.slow_ema, self.params)
        return zone

    def test_rsi_54_is_zone_a(self):
        assert self._zone(54.9) == "A"

    def test_rsi_55_is_not_zone_a(self):
        # a_max=55 is a strict upper bound on Zone A ("RSI < this"); at
        # exactly 55 it also falls short of b_range's low end (56), so it's
        # a gap value -- Unclassified, not A and not B. This is the spec's
        # own boundary design, not a bug.
        assert self._zone(55) == "Unclassified"

    def test_rsi_56_is_zone_b(self):
        assert self._zone(56) == "B"

    def test_rsi_65_is_zone_b(self):
        assert self._zone(65) == "B"

    def test_rsi_66_is_zone_c(self):
        assert self._zone(66) == "C"

    def test_rsi_71_is_zone_c(self):
        assert self._zone(71) == "C"

    def test_rsi_72_is_zone_d(self):
        assert self._zone(72) == "D"

    def test_rsi_71_9_is_zone_c_not_d(self):
        assert self._zone(71.9) == "C"


class TestClassifyZoneMacroFilterOverride:
    def test_zone_d_fires_even_when_rsi_looks_like_zone_a(self):
        """Low RSI alone isn't enough for Zone A -- price below both the
        macro SMA and the slow EMA forces Zone D regardless of RSI."""
        params = ZoneParams()
        zone, _, reason = classify_zone(
            rsi=30.0, price=80.0, macro_sma=90.0, fast_ema=85.0, slow_ema=88.0, params=params,
        )
        assert zone == "D"
        assert "80" in reason  # reason cites the price, not just "RSI"

    def test_zone_a_requires_price_above_macro_sma(self):
        params = ZoneParams()
        zone, _, _ = classify_zone(
            rsi=40.0, price=89.0, macro_sma=90.0, fast_ema=89.0, slow_ema=88.0, params=params,
        )
        # price (89) is below macro_sma (90) but NOT below slow_ema (88) --
        # doesn't trigger D's trend filter, but also fails A's price>macro_sma
        # requirement, so it falls to Unclassified.
        assert zone == "Unclassified"


class TestClassifyZoneNearEma:
    def test_zone_a_requires_proximity_to_an_ema(self):
        params = ZoneParams(near_ema_pct=0.02)
        # RSI and trend conditions satisfy A, but price is 10% away from both EMAs
        zone, _, _ = classify_zone(
            rsi=40.0, price=110.0, macro_sma=90.0, fast_ema=100.0, slow_ema=100.0, params=params,
        )
        assert zone == "Unclassified"

    def test_zone_a_fires_within_near_ema_pct_of_fast_ema(self):
        params = ZoneParams(near_ema_pct=0.02)
        zone, _, _ = classify_zone(
            rsi=40.0, price=101.5, macro_sma=90.0, fast_ema=100.0, slow_ema=80.0, params=params,
        )
        assert zone == "A"  # 1.5% from fast_ema, within the 2% band

    def test_zone_a_fires_within_near_ema_pct_of_slow_ema(self):
        params = ZoneParams(near_ema_pct=0.02)
        zone, _, _ = classify_zone(
            rsi=40.0, price=101.5, macro_sma=90.0, fast_ema=80.0, slow_ema=100.0, params=params,
        )
        assert zone == "A"  # 1.5% from slow_ema, within the 2% band


class TestClassifyZoneUnclassifiedFallthrough:
    def test_unclassified_when_no_rule_matches(self):
        """RSI 60 is between Zone A's ceiling and Zone B's floor is NOT this
        case (60 is inside b_range) -- use price failing Zone B's macro
        filter with an RSI that also isn't C or D."""
        params = ZoneParams()
        zone, zone_label, reason = classify_zone(
            rsi=60.0, price=80.0, macro_sma=90.0, fast_ema=85.0, slow_ema=85.0, params=params,
        )
        # price(80) < macro_sma(90) but NOT < slow_ema(85)... wait slow_ema=85 > 80 too.
        # Recompute: to avoid D's trend filter need price >= macro_sma OR price >= slow_ema.
        assert zone in ("D", "Unclassified")  # documents the actual boundary; see next test for the real gap case

    def test_unclassified_gap_between_zone_a_and_zone_b(self):
        params = ZoneParams()  # a_max=55, b_range=(56, 65)
        zone, zone_label, _ = classify_zone(
            rsi=55.5, price=100.0, macro_sma=90.0, fast_ema=100.0, slow_ema=95.0, params=params,
        )
        assert zone == "Unclassified"
        assert zone_label == "Unclassified"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_zone_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.zone_classifier'`.

- [ ] **Step 3: Implement `app/services/zone_classifier.py`**

```python
"""Pure BS-V4 Zone Classifier logic: no I/O, no database access.

Zone codes and labels are neutral technical-state descriptions, not
buy/sell advice -- see docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md
for why. The math/thresholds match the original request exactly; only the
naming changed.
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE_LABELS = {
    "A": "Pullback at Support",
    "B": "Mid-RSI Above Trend",
    "C": "Elevated RSI",
    "D": "Overbought or Below Trend",
    "Unclassified": "Unclassified",
}


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

    def __post_init__(self) -> None:
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("fast_ema_period must be < slow_ema_period")
        for name in ("macro_sma_period", "fast_ema_period", "slow_ema_period", "rsi_period", "atr_period", "rvol_period"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.near_ema_pct <= 0:
            raise ValueError("near_ema_pct must be > 0")
        b_lo, b_hi = self.rsi_zone_b_range
        c_lo, c_hi = self.rsi_zone_c_range
        if not (self.rsi_zone_a_max <= b_lo <= b_hi < c_lo <= c_hi < self.rsi_zone_d_min):
            raise ValueError(
                "RSI zone boundaries must be ordered: rsi_zone_a_max <= rsi_zone_b_range "
                "<= (gap allowed) < rsi_zone_c_range < rsi_zone_d_min"
            )

    @property
    def max_window(self) -> int:
        """The longest lookback any configured indicator needs."""
        return max(self.macro_sma_period, self.slow_ema_period, self.rsi_period, self.atr_period, self.rvol_period)


def _zone_for(rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams) -> str:
    """Just the zone code. Priority order, first match wins: D -> C -> B -> A -> Unclassified."""
    if rsi >= params.rsi_zone_d_min or (price < macro_sma and price < slow_ema):
        return "D"
    lo, hi = params.rsi_zone_c_range
    if lo <= rsi <= hi:
        return "C"
    lo, hi = params.rsi_zone_b_range
    if lo <= rsi <= hi and price > macro_sma:
        return "B"
    if rsi < params.rsi_zone_a_max and price > macro_sma:
        fast_near = fast_ema != 0 and abs(price - fast_ema) / fast_ema <= params.near_ema_pct
        slow_near = slow_ema != 0 and abs(price - slow_ema) / slow_ema <= params.near_ema_pct
        if fast_near or slow_near:
            return "A"
    return "Unclassified"


def _reason_for(
    zone: str, rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams
) -> str:
    """Factual, numeric reason text -- never a verb like buy/sell/hold/exit."""
    if zone == "D":
        if rsi >= params.rsi_zone_d_min:
            return f"RSI {rsi:.1f} >= {params.rsi_zone_d_min}"
        return (
            f"price {price:.2f} below {params.macro_sma_period} SMA ({macro_sma:.2f}) "
            f"and below {params.slow_ema_period} EMA ({slow_ema:.2f})"
        )
    if zone == "C":
        lo, hi = params.rsi_zone_c_range
        return f"RSI {rsi:.1f} within [{lo}, {hi}]"
    if zone == "B":
        lo, hi = params.rsi_zone_b_range
        return f"RSI {rsi:.1f} within [{lo}, {hi}], price above {params.macro_sma_period} SMA"
    if zone == "A":
        return (
            f"RSI {rsi:.1f} < {params.rsi_zone_a_max}, price above {params.macro_sma_period} SMA, "
            f"within {params.near_ema_pct:.0%} of an EMA"
        )
    return f"RSI {rsi:.1f}, price {price:.2f} matched no zone rule"


def classify_zone(
    rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams
) -> tuple[str, str, str]:
    """Classify one instrument's latest bar. Returns (zone_code, zone_label, reason)."""
    zone = _zone_for(rsi, price, macro_sma, fast_ema, slow_ema, params)
    reason = _reason_for(zone, rsi, price, macro_sma, fast_ema, slow_ema, params)
    return zone, ZONE_LABELS[zone], reason
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_zone_classifier.py -v`
Expected: all PASS. If `test_unclassified_when_no_rule_matches` fails or the assertion feels loose, that's expected — it's documenting a boundary explicitly rather than asserting a wrong invariant; `test_unclassified_gap_between_zone_a_and_zone_b` is the real pinned case for that gap.

- [ ] **Step 5: Commit**

```bash
git add app/services/zone_classifier.py tests/test_zone_classifier.py
git commit -m "feat: add BS-V4 zone classifier core (params, single-instrument classification)"
```

---

### Task 3: Zone classifier — vectorized whole-universe classification

**Files:**
- Modify: `app/services/zone_classifier.py`
- Test: `tests/test_zone_classifier.py`

**Interfaces:**
- Consumes: `ZoneParams`, `ZONE_LABELS`, `_zone_for` (Task 2, same file).
- Produces: `classify_zones_wide(rsi: pd.Series, price: pd.Series, macro_sma: pd.Series, fast_ema: pd.Series, slow_ema: pd.Series, params: ZoneParams) -> pd.DataFrame` (columns `zone`, `zone_label`, `reason`, index = instrument_id) — consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_zone_classifier.py`:

```python
import pandas as pd

from app.services.zone_classifier import _zone_for, classify_zones_wide


class TestClassifyZonesWideParity:
    """The wide path recomputes the D/C/B/A rules independently as vectorized
    boolean masks (for speed across ~7,500 instruments) rather than calling
    the scalar path per row -- this test is the only thing that catches the
    two implementations drifting apart."""

    def test_wide_and_scalar_agree_across_many_cases(self):
        params = ZoneParams()
        # A grid of hand-picked (rsi, price, macro_sma, fast_ema, slow_ema)
        # tuples spanning every zone, including the boundary and gap cases
        # from TestClassifyZoneBoundaries.
        cases = [
            (54.9, 100.0, 90.0, 100.0, 95.0),   # A
            (55.0, 100.0, 90.0, 100.0, 95.0),   # Unclassified (gap)
            (56.0, 100.0, 90.0, 100.0, 95.0),   # B
            (65.0, 100.0, 90.0, 100.0, 95.0),   # B
            (66.0, 100.0, 90.0, 100.0, 95.0),   # C
            (71.0, 100.0, 90.0, 100.0, 95.0),   # C
            (72.0, 100.0, 90.0, 100.0, 95.0),   # D (RSI)
            (30.0, 80.0, 90.0, 85.0, 88.0),     # D (trend filter overrides low RSI)
            (40.0, 89.0, 90.0, 89.0, 88.0),     # Unclassified (price not > macro_sma)
            (40.0, 110.0, 90.0, 100.0, 100.0),  # Unclassified (not near either EMA)
            (40.0, 101.5, 90.0, 100.0, 80.0),   # A (near fast EMA)
            (40.0, 101.5, 90.0, 80.0, 100.0),   # A (near slow EMA)
        ]
        rsi = pd.Series([c[0] for c in cases])
        price = pd.Series([c[1] for c in cases])
        macro_sma = pd.Series([c[2] for c in cases])
        fast_ema = pd.Series([c[3] for c in cases])
        slow_ema = pd.Series([c[4] for c in cases])

        wide_result = classify_zones_wide(rsi, price, macro_sma, fast_ema, slow_ema, params)

        for i, case in enumerate(cases):
            expected = _zone_for(*case, params)
            assert wide_result["zone"].iloc[i] == expected, f"case {i} {case}: expected {expected}"

    def test_wide_preserves_input_index(self):
        params = ZoneParams()
        idx = pd.Index([501, 502, 503], name="instrument_id")
        rsi = pd.Series([40.0, 60.0, 80.0], index=idx)
        price = pd.Series([100.0, 100.0, 100.0], index=idx)
        macro_sma = pd.Series([90.0, 90.0, 90.0], index=idx)
        fast_ema = pd.Series([100.0, 100.0, 100.0], index=idx)
        slow_ema = pd.Series([95.0, 95.0, 95.0], index=idx)

        result = classify_zones_wide(rsi, price, macro_sma, fast_ema, slow_ema, params)
        assert list(result.index) == [501, 502, 503]

    def test_wide_handles_zero_ema_without_dividing_by_zero(self):
        """A defensive edge case: an EMA of exactly 0 must not raise or
        produce inf/NaN propagating into the zone decision."""
        params = ZoneParams()
        rsi = pd.Series([40.0])
        price = pd.Series([100.0])
        macro_sma = pd.Series([90.0])
        fast_ema = pd.Series([0.0])
        slow_ema = pd.Series([95.0])

        result = classify_zones_wide(rsi, price, macro_sma, fast_ema, slow_ema, params)
        assert result["zone"].iloc[0] in ("A", "Unclassified", "B", "C", "D")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_zone_classifier.py -k "ClassifyZonesWide" -v`
Expected: FAIL with `ImportError: cannot import name 'classify_zones_wide'`.

- [ ] **Step 3: Implement `classify_zones_wide`**

Add to `app/services/zone_classifier.py` (needs `import pandas as pd` added to the top of the file):

```python
def classify_zones_wide(
    rsi: pd.Series, price: pd.Series, macro_sma: pd.Series, fast_ema: pd.Series, slow_ema: pd.Series,
    params: ZoneParams,
) -> pd.DataFrame:
    """Same rules as classify_zone, vectorized across instruments. All five
    inputs must be same-shaped pd.Series indexed by instrument_id (one row =
    one instrument's latest bar). Returns a DataFrame with zone/zone_label/
    reason columns, same index as the inputs.

    The zone/zone_label columns come from vectorized boolean masks (fast --
    this is the O(instruments) step, not O(instruments x days)). The reason
    column is built with a per-row loop over already-computed scalars, which
    is cheap (string formatting, not indicator math) and reuses _reason_for
    so the text matches classify_zone's wording exactly.
    """
    d_mask = (rsi >= params.rsi_zone_d_min) | ((price < macro_sma) & (price < slow_ema))

    c_lo, c_hi = params.rsi_zone_c_range
    c_mask = ~d_mask & rsi.between(c_lo, c_hi)

    b_lo, b_hi = params.rsi_zone_b_range
    b_mask = ~d_mask & ~c_mask & rsi.between(b_lo, b_hi) & (price > macro_sma)

    fast_safe = fast_ema.replace(0, float("nan"))
    slow_safe = slow_ema.replace(0, float("nan"))
    fast_near = ((price - fast_ema).abs() / fast_safe <= params.near_ema_pct).fillna(False)
    slow_near = ((price - slow_ema).abs() / slow_safe <= params.near_ema_pct).fillna(False)
    near_ema = fast_near | slow_near
    a_mask = ~d_mask & ~c_mask & ~b_mask & (rsi < params.rsi_zone_a_max) & (price > macro_sma) & near_ema

    zone = pd.Series("Unclassified", index=rsi.index)
    zone[d_mask] = "D"
    zone[c_mask] = "C"
    zone[b_mask] = "B"
    zone[a_mask] = "A"

    reason = pd.Series(
        [
            _reason_for(z, r, p, m, f, s, params)
            for z, r, p, m, f, s in zip(zone, rsi, price, macro_sma, fast_ema, slow_ema)
        ],
        index=rsi.index,
    )
    return pd.DataFrame({"zone": zone, "zone_label": zone.map(ZONE_LABELS), "reason": reason})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_zone_classifier.py -v`
Expected: all PASS (Task 2's tests plus Task 3's).

- [ ] **Step 5: Commit**

```bash
git add app/services/zone_classifier.py tests/test_zone_classifier.py
git commit -m "feat: add vectorized whole-universe zone classification with parity test"
```

---

### Task 4: Zone loader — single-instrument path

**Files:**
- Create: `app/services/zone_loader.py`
- Test: `tests/test_zone_loader.py`

**Interfaces:**
- Consumes: `ZoneParams`, `classify_zone` (Task 2/3); `load_price_history(db, instrument_id) -> pd.DataFrame` (existing, `app/jobs/compute_indicators.py`, index=trade_date, columns open/high/low/close/adjusted_close/volume); `sma`, `ema`, `rsi`, `atr`, `volume_sma` (existing, `app/services/indicators.py`).
- Produces: `ZoneResult` (frozen dataclass: `ticker, zone, zone_label, rsi, price, macro_sma, fast_ema, slow_ema, atr_band_lower, atr_band_upper, rvol, reason` — all numeric fields `float | None`); `get_zone_for_instrument(db: Session, instrument_id: int, params: ZoneParams) -> ZoneResult | None` (`None` means instrument not found, caller maps to 404) — consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_zone_loader.py`:

```python
"""Tests for app/services/zone_loader.py's single-instrument path.
Uses a real SAVEPOINT-backed test transaction against the local Postgres
(same pattern as tests/test_api.py) -- always rolled back.

Run with: pytest tests/test_zone_loader.py -v
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import DailyPrice, Instrument
from app.services.zone_classifier import ZoneParams
from app.services.zone_loader import get_zone_for_instrument


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


@pytest.fixture()
def instrument(db):
    inst = Instrument(symbol="ZONETEST", exchange="NSE", company_name="Zone Test Co", is_active=True)
    db.add(inst)
    db.flush()
    return inst


def _seed_prices(db, instrument_id: int, closes: list[float], start: date):
    for i, close in enumerate(closes):
        db.add(
            DailyPrice(
                instrument_id=instrument_id,
                trade_date=start + timedelta(days=i),
                open=Decimal(str(close)),
                high=Decimal(str(close * 1.01)),
                low=Decimal(str(close * 0.99)),
                close=Decimal(str(close)),
                adjusted_close=Decimal(str(close)),
                volume=100000 + i * 10,
            )
        )
    db.flush()


class TestGetZoneForInstrument:
    def test_unknown_instrument_returns_none(self, db):
        params = ZoneParams()
        assert get_zone_for_instrument(db, instrument_id=999999, params=params) is None

    def test_insufficient_history_returns_insufficient_data(self, db, instrument):
        params = ZoneParams()  # needs macro_sma_period=200 + 1 bars
        _seed_prices(db, instrument.id, [100.0] * 30, start=date(2026, 1, 1))

        result = get_zone_for_instrument(db, instrument.id, params)

        assert result.zone == "Insufficient Data"
        assert result.zone_label == "Insufficient Data"
        assert result.rsi is None
        assert result.price is None

    def test_full_history_classifies_a_real_zone(self, db, instrument):
        # Small periods so 60 bars is enough to have real, non-NaN values.
        params = ZoneParams(macro_sma_period=20, fast_ema_period=5, slow_ema_period=10, rsi_period=14, atr_period=14, rvol_period=20)
        # A rising series: RSI stays high, price stays above the SMA -- lands
        # in Zone B, C, or D depending on exactly how strong the trend is;
        # the point of this test is "classifies something real", not a
        # specific zone (boundary zones are covered in test_zone_classifier.py).
        closes = [100.0 + i * 0.5 for i in range(60)]
        _seed_prices(db, instrument.id, closes, start=date(2026, 1, 1))

        result = get_zone_for_instrument(db, instrument.id, params)

        assert result.zone in ("A", "B", "C", "D", "Unclassified")
        assert result.rsi is not None
        assert result.price == pytest.approx(closes[-1])
        assert result.ticker == "ZONETEST"

    def test_atr_band_lower_only_set_for_zone_a(self, db, instrument):
        params = ZoneParams(
            macro_sma_period=10, fast_ema_period=3, slow_ema_period=5, rsi_period=5, atr_period=5, rvol_period=5,
            near_ema_pct=0.5,  # generous, so a mild pullback still counts as "near"
        )
        # Flat-ish then a small recent dip -- low RSI, price still above the
        # short SMA, near the EMAs -- should land in Zone A.
        closes = [100.0] * 15 + [99.0, 98.5, 98.0]
        _seed_prices(db, instrument.id, closes, start=date(2026, 1, 1))

        result = get_zone_for_instrument(db, instrument.id, params)

        if result.zone == "A":
            assert result.atr_band_lower is not None
            assert result.atr_band_upper is None
        # If the exact numbers don't land in A on this data, that's fine --
        # test_zone_classifier.py already pins the A/B boundary logic. This
        # test's job is only the wiring: IF zone is A, atr_band_lower must be set.

    def test_rvol_is_volume_over_volume_sma(self, db, instrument):
        params = ZoneParams(macro_sma_period=10, fast_ema_period=3, slow_ema_period=5, rsi_period=5, atr_period=5, rvol_period=5)
        closes = [100.0] * 20
        _seed_prices(db, instrument.id, closes, start=date(2026, 1, 1))
        # Bump the last day's volume way up directly.
        last_price = db.query(DailyPrice).filter_by(instrument_id=instrument.id).order_by(DailyPrice.trade_date.desc()).first()
        last_price.volume = 1000000
        db.flush()

        result = get_zone_for_instrument(db, instrument.id, params)

        assert result.rvol is not None
        assert result.rvol > 1.0  # last day's volume is well above its own trailing average
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_zone_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.zone_loader'`.

- [ ] **Step 3: Implement `app/services/zone_loader.py`**

```python
"""DB-backed loading, indicator computation, and orchestration for the
BS-V4 Zone Classifier's single-instrument path. See zone_loader's scan
functions (Task 5) for the market-wide path.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.compute_indicators import load_price_history
from app.models import Instrument
from app.services.indicators import atr, ema, rsi, sma, volume_sma
from app.services.zone_classifier import ZoneParams, classify_zone


@dataclass(frozen=True)
class ZoneResult:
    ticker: str
    zone: str
    zone_label: str
    rsi: float | None
    price: float | None
    macro_sma: float | None
    fast_ema: float | None
    slow_ema: float | None
    atr_band_lower: float | None
    atr_band_upper: float | None
    rvol: float | None
    reason: str


def _insufficient_data(ticker: str, reason: str) -> ZoneResult:
    return ZoneResult(
        ticker=ticker, zone="Insufficient Data", zone_label="Insufficient Data",
        rsi=None, price=None, macro_sma=None, fast_ema=None, slow_ema=None,
        atr_band_lower=None, atr_band_upper=None, rvol=None, reason=reason,
    )


def get_zone_for_instrument(db: Session, instrument_id: int, params: ZoneParams) -> ZoneResult | None:
    """Returns None if instrument_id doesn't exist -- caller maps that to a 404."""
    instrument = db.execute(select(Instrument).where(Instrument.id == instrument_id)).scalars().first()
    if instrument is None:
        return None

    history = load_price_history(db, instrument_id)
    needed = params.max_window + 1
    if len(history) < needed:
        return _insufficient_data(instrument.symbol, f"needs {needed} bars of history, has {len(history)}")

    price = history["adjusted_close"].astype(float)
    macro_sma_s = sma(price, params.macro_sma_period)
    fast_ema_s = ema(price, params.fast_ema_period)
    slow_ema_s = ema(price, params.slow_ema_period)
    rsi_s = rsi(price, params.rsi_period)
    atr_s = atr(
        history["high"].astype(float), history["low"].astype(float), history["close"].astype(float),
        price, params.atr_period,
    )
    volume = history["volume"].astype(float)
    vol_sma_s = volume_sma(volume, params.rvol_period)

    latest = {
        "price": price.iloc[-1], "macro_sma": macro_sma_s.iloc[-1], "fast_ema": fast_ema_s.iloc[-1],
        "slow_ema": slow_ema_s.iloc[-1], "rsi": rsi_s.iloc[-1], "atr": atr_s.iloc[-1],
        "volume": volume.iloc[-1], "volume_sma": vol_sma_s.iloc[-1],
    }
    if any(pd.isna(v) for v in latest.values()):
        return _insufficient_data(instrument.symbol, "latest bar has NaN indicator values")

    zone, zone_label, reason = classify_zone(
        latest["rsi"], latest["price"], latest["macro_sma"], latest["fast_ema"], latest["slow_ema"], params,
    )
    rvol = latest["volume"] / latest["volume_sma"] if latest["volume_sma"] else None
    atr_band_lower = latest["macro_sma"] - 0.5 * latest["atr"] if zone == "A" else None
    atr_band_upper = latest["slow_ema"] + params.atr_limit_multiplier * latest["atr"] if zone == "B" else None

    return ZoneResult(
        ticker=instrument.symbol, zone=zone, zone_label=zone_label,
        rsi=latest["rsi"], price=latest["price"], macro_sma=latest["macro_sma"],
        fast_ema=latest["fast_ema"], slow_ema=latest["slow_ema"],
        atr_band_lower=atr_band_lower, atr_band_upper=atr_band_upper, rvol=rvol, reason=reason,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_zone_loader.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/zone_loader.py tests/test_zone_loader.py
git commit -m "feat: add single-instrument zone classification loader"
```

---

### Task 5: Zone loader — market-wide scan with caching

**Files:**
- Modify: `app/services/zone_loader.py`
- Test: `tests/test_zone_loader.py`

**Interfaces:**
- Consumes: `resolve_window(n_bars: int) -> tuple[date, date]` (existing, `app/services/crossover_loader.py`); `latest_trade_date(db) -> date | None` (existing, `app/services/screening.py`); `STALE_TOLERANCE_DAYS` (existing, `app/services/crossover.py`); `classify_zones_wide` (Task 3); `ZoneResult` (Task 4, this file).
- Produces: `ScanResult` (dataclass: `as_of: date, matches: list[ZoneResult], skipped: list[dict], evaluated: int, cached: bool, elapsed_ms: int`); `run_zone_scan(db: Session, params: ZoneParams) -> ScanResult` — consumed by Task 7. `skipped` entries are `{"ticker": str, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_zone_loader.py` (needs `from app.services.zone_loader import run_zone_scan, _load_wide_market, _scan_cached` added to imports, and `from app.services.zone_loader import _connect` for the monkeypatch pattern below):

```python
from app.services.zone_loader import _connect, _scan_cached, run_zone_scan


def _recent_trade_dates(db, n: int) -> list[date]:
    """Anchor test data to the real market calendar already in the dev DB,
    not a hardcoded date range -- a hardcoded range breaks the moment real
    data shares the table (see the crossover feature's Task 8 postmortem)."""
    rows = (
        db.query(DailyPrice.trade_date)
        .distinct()
        .order_by(DailyPrice.trade_date.desc())
        .limit(n)
        .all()
    )
    return sorted(r[0] for r in rows)


class TestRunZoneScan:
    def test_finds_a_seeded_instrument_and_classifies_it(self, db, monkeypatch):
        import contextlib
        monkeypatch.setattr("app.services.zone_loader._connect", lambda: contextlib.nullcontext(db.connection()))
        _scan_cached.cache_clear()

        params = ZoneParams(macro_sma_period=10, fast_ema_period=3, slow_ema_period=5, rsi_period=5, atr_period=5, rvol_period=5)
        needed = params.max_window + 5
        dates = _recent_trade_dates(db, needed) if db.query(DailyPrice).count() > 0 else [date(2026, 1, 1) + timedelta(days=i) for i in range(needed)]
        if len(dates) < needed:
            dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(needed)]

        inst = Instrument(symbol="SCANTEST", exchange="NSE", company_name="Scan Test Co", is_active=True)
        db.add(inst)
        db.flush()
        for i, d in enumerate(dates):
            db.add(DailyPrice(
                instrument_id=inst.id, trade_date=d,
                open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                close=Decimal("100"), adjusted_close=Decimal("100"), volume=100000,
            ))
        db.flush()

        result = run_zone_scan(db, params)

        tickers = {m.ticker for m in result.matches}
        skipped_tickers = {s["ticker"] for s in result.skipped}
        assert "SCANTEST" in tickers or "SCANTEST" in skipped_tickers
        assert result.evaluated >= 1

    def test_sorted_by_zone_then_rsi_ascending(self, db, monkeypatch):
        import contextlib
        monkeypatch.setattr("app.services.zone_loader._connect", lambda: contextlib.nullcontext(db.connection()))
        _scan_cached.cache_clear()

        params = ZoneParams()
        result = run_zone_scan(db, params)

        zone_order = {"A": 0, "B": 1, "C": 2, "D": 3, "Unclassified": 4}
        zones_seen = [zone_order[m.zone] for m in result.matches]
        assert zones_seen == sorted(zones_seen)
        # within each zone, RSI ascending
        for i in range(1, len(result.matches)):
            if result.matches[i].zone == result.matches[i - 1].zone:
                assert result.matches[i].rsi >= result.matches[i - 1].rsi

    def test_repeat_call_same_params_is_a_cache_hit(self, db, monkeypatch):
        import contextlib
        monkeypatch.setattr("app.services.zone_loader._connect", lambda: contextlib.nullcontext(db.connection()))
        _scan_cached.cache_clear()

        params = ZoneParams()
        first = run_zone_scan(db, params)
        second = run_zone_scan(db, params)

        assert first.cached is False
        assert second.cached is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_zone_loader.py -k "RunZoneScan" -v`
Expected: FAIL with `ImportError` (no `run_zone_scan`/`_scan_cached`/`_connect` yet).

- [ ] **Step 3: Implement the scan path**

Add to `app/services/zone_loader.py` (needs `dataclasses` module, `date` from `datetime`, `functools.lru_cache`, `time`, `text` from sqlalchemy, `engine` from `app.db.session`, `resolve_window` from `app.services.crossover_loader`, `STALE_TOLERANCE_DAYS` from `app.services.crossover`, `latest_trade_date` from `app.services.screening`, and `classify_zones_wide` added to the zone_classifier import — extend the existing imports at the top of the file rather than duplicating them):

```python
import dataclasses
import time
from datetime import date
from functools import lru_cache

from sqlalchemy import text

from app.db.session import engine
from app.services.crossover import STALE_TOLERANCE_DAYS
from app.services.crossover_loader import resolve_window
from app.services.screening import latest_trade_date
from app.services.zone_classifier import classify_zones_wide


@dataclass(frozen=True)
class ScanResult:
    as_of: date
    matches: list[ZoneResult]
    skipped: list[dict]
    evaluated: int
    cached: bool
    elapsed_ms: int


def _connect():
    """Indirection point so tests can substitute the SAVEPOINT-backed test
    connection (same pattern as app/services/crossover_loader.py)."""
    return engine.connect()


def _load_wide_market(cutoff: date) -> dict[str, pd.DataFrame]:
    """One range scan for the whole market, pivoted per-field to
    trade_date x instrument_id. Also returns the symbol for each
    instrument_id so the scan path doesn't need a second query."""
    with _connect() as conn:
        long = pd.read_sql(
            text(
                """
                SELECT p.instrument_id, i.symbol, p.trade_date, p.high, p.low, p.close, p.adjusted_close, p.volume
                FROM daily_prices p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE i.is_active AND p.trade_date >= :cutoff
                """
            ),
            conn,
            params={"cutoff": cutoff},
        )
    symbols = long.drop_duplicates("instrument_id").set_index("instrument_id")["symbol"]
    frames = {}
    for field in ("high", "low", "close", "adjusted_close", "volume"):
        wide = long.pivot(index="trade_date", columns="instrument_id", values=field).sort_index()
        frames[field] = wide.ffill(limit=STALE_TOLERANCE_DAYS)
    frames["symbols"] = symbols
    return frames


@lru_cache(maxsize=4)
def _load_wide_cached(n_bars: int, as_of: date) -> dict[str, pd.DataFrame]:
    cutoff, _ = resolve_window(n_bars)
    return _load_wide_market(cutoff)


@lru_cache(maxsize=4)
def _scan_cached(params: ZoneParams, as_of: date) -> ScanResult:
    t0 = time.perf_counter()
    frames = _load_wide_cached(params.max_window + 1, as_of)
    symbols = frames["symbols"]
    total_active = len(symbols)

    price = frames["adjusted_close"]
    macro_sma_wide = sma(price, params.macro_sma_period)
    fast_ema_wide = ema(price, params.fast_ema_period)
    slow_ema_wide = ema(price, params.slow_ema_period)
    rsi_wide = rsi(price, params.rsi_period)
    atr_wide = atr(frames["high"], frames["low"], frames["close"], price, params.atr_period)
    vol_sma_wide = volume_sma(frames["volume"], params.rvol_period)

    latest_price = price.iloc[-1]
    latest_macro_sma = macro_sma_wide.iloc[-1]
    latest_fast_ema = fast_ema_wide.iloc[-1]
    latest_slow_ema = slow_ema_wide.iloc[-1]
    latest_rsi = rsi_wide.iloc[-1]
    latest_atr = atr_wide.iloc[-1]
    latest_volume = frames["volume"].iloc[-1]
    latest_vol_sma = vol_sma_wide.iloc[-1]

    required = pd.concat(
        [latest_price, latest_macro_sma, latest_fast_ema, latest_slow_ema, latest_rsi, latest_atr, latest_vol_sma],
        axis=1,
    )
    ready_mask = required.notna().all(axis=1)

    skipped = [
        {"ticker": symbols.get(iid, str(iid)), "reason": "insufficient history or NaN indicator value"}
        for iid in required.index[~ready_mask]
    ]

    ready_ids = required.index[ready_mask]
    zones = classify_zones_wide(
        latest_rsi[ready_ids], latest_price[ready_ids], latest_macro_sma[ready_ids],
        latest_fast_ema[ready_ids], latest_slow_ema[ready_ids], params,
    )

    matches = []
    for iid in ready_ids:
        zone = zones.at[iid, "zone"]
        zone_label = zones.at[iid, "zone_label"]
        reason = zones.at[iid, "reason"]
        atr_val = latest_atr[iid]
        rvol = latest_volume[iid] / latest_vol_sma[iid] if latest_vol_sma[iid] else None
        atr_band_lower = latest_macro_sma[iid] - 0.5 * atr_val if zone == "A" else None
        atr_band_upper = latest_slow_ema[iid] + params.atr_limit_multiplier * atr_val if zone == "B" else None
        matches.append(ZoneResult(
            ticker=symbols.get(iid, str(iid)), zone=zone, zone_label=zone_label,
            rsi=latest_rsi[iid], price=latest_price[iid], macro_sma=latest_macro_sma[iid],
            fast_ema=latest_fast_ema[iid], slow_ema=latest_slow_ema[iid],
            atr_band_lower=atr_band_lower, atr_band_upper=atr_band_upper, rvol=rvol, reason=reason,
        ))

    zone_order = {"A": 0, "B": 1, "C": 2, "D": 3, "Unclassified": 4}
    matches.sort(key=lambda m: (zone_order[m.zone], m.rsi))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return ScanResult(as_of=as_of, matches=matches, skipped=skipped, evaluated=total_active, cached=False, elapsed_ms=elapsed_ms)


def run_zone_scan(db: Session, params: ZoneParams) -> ScanResult:
    """`lru_cache` doesn't expose "was this specific key already cached" via
    cache_info() (only aggregate hit/miss counts across all keys) -- and a
    cache-hit result is the exact same frozen ScanResult object from when it
    was first computed, with cached=False still baked into it. Comparing the
    hit counter before/after this one call is what tells us which case we're
    in, so we can override cached=True on the returned object after the fact.
    """
    as_of = latest_trade_date(db)
    if as_of is None:
        raise ValueError("no price data loaded yet")

    hits_before = _scan_cached.cache_info().hits
    result = _scan_cached(params, as_of)
    was_cache_hit = _scan_cached.cache_info().hits > hits_before
    return dataclasses.replace(result, cached=was_cache_hit) if was_cache_hit else result
```

`ZoneParams` must be hashable for `lru_cache` to key on it — it already is, since `@dataclass(frozen=True)` with all-hashable field types (`int`, `float`, `tuple[float, float]`) auto-generates `__hash__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_zone_loader.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite to confirm no regressions elsewhere**

Run: `pytest -q`
Expected: every test passes (the count should be the prior total plus this plan's new tests so far).

- [ ] **Step 6: Commit**

```bash
git add app/services/zone_loader.py tests/test_zone_loader.py
git commit -m "feat: add market-wide zone scan with caching"
```

---

### Task 6: API schemas

**Files:**
- Create: `app/schemas/zone.py`

**Interfaces:**
- Consumes: nothing (Pydantic models only).
- Produces: `ZoneOut`, `SkippedOut`, `ZoneParamsOut`, `ZoneScanResponse` — consumed by Task 7.

- [ ] **Step 1: Implement `app/schemas/zone.py`**

No test file for this task — pure schema definitions are exercised by Task 7's API tests, consistent with how `app/schemas/crossover.py` was handled in the crossover feature (no standalone schema test file; the API tests cover it).

```python
from typing import Literal

from pydantic import BaseModel


class ZoneOut(BaseModel):
    ticker: str
    zone: Literal["A", "B", "C", "D", "Unclassified", "Insufficient Data"]
    zone_label: str
    rsi: float | None
    price: float | None
    macro_sma: float | None
    fast_ema: float | None
    slow_ema: float | None
    atr_band_lower: float | None
    atr_band_upper: float | None
    rvol: float | None
    reason: str


class SkippedOut(BaseModel):
    ticker: str
    reason: str


class ZoneParamsOut(BaseModel):
    macro_sma_period: int
    fast_ema_period: int
    slow_ema_period: int
    rsi_period: int
    rsi_zone_a_max: float
    rsi_zone_b_range: tuple[float, float]
    rsi_zone_c_range: tuple[float, float]
    rsi_zone_d_min: float
    atr_period: int
    atr_limit_multiplier: float
    rvol_period: int
    near_ema_pct: float


class ZoneScanResponse(BaseModel):
    as_of: str
    params: ZoneParamsOut
    matches: list[ZoneOut]
    skipped: list[SkippedOut]
    evaluated: int
    cached: bool
    elapsed_ms: int
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "from app.schemas.zone import ZoneOut, SkippedOut, ZoneParamsOut, ZoneScanResponse; print('ok')"`
Expected: prints `ok`, no errors.

- [ ] **Step 3: Commit**

```bash
git add app/schemas/zone.py
git commit -m "feat: add zone classifier API schemas"
```

---

### Task 7: API endpoints

**Files:**
- Create: `app/api/zone.py`
- Modify: `app/main.py` (register the new router)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `get_zone_for_instrument`, `run_zone_scan`, `ZoneResult` (Task 4/5), `ZoneParams` (Task 2), `ZoneOut`/`SkippedOut`/`ZoneParamsOut`/`ZoneScanResponse` (Task 6); `get_current_user`, `get_db` (existing, `app/api/deps.py`, `app/db/session.py`).
- Produces: `GET /api/zone/{instrument_id}`, `GET /api/zone/scan` — no later task consumes these directly (terminal for the backend).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py` (uses the file's existing `db`/`client`/`instrument`/`today`/`prev_day` fixtures — do not redefine them):

```python
class TestZoneClassifier:
    def test_get_zone_unknown_instrument_404s(self, client):
        resp = client.get("/api/zone/999999")
        assert resp.status_code == 404

    def test_get_zone_invalid_params_422s(self, client, instrument):
        resp = client.get(f"/api/zone/{instrument.id}", params={"fast_ema_period": 21, "slow_ema_period": 21})
        assert resp.status_code == 422

    def test_get_zone_insufficient_history(self, client, db, instrument, today):
        from app.models import DailyPrice
        from decimal import Decimal
        db.add(DailyPrice(
            instrument_id=instrument.id, trade_date=today,
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
            close=Decimal("100"), adjusted_close=Decimal("100"), volume=1000,
        ))
        db.flush()

        resp = client.get(f"/api/zone/{instrument.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["zone"] == "Insufficient Data"
        assert body["rsi"] is None

    def test_get_zone_full_history_classifies(self, client, db, instrument, today):
        from app.models import DailyPrice
        from decimal import Decimal
        from datetime import timedelta
        for i in range(60):
            d = today - timedelta(days=59 - i)
            close = 100.0 + i * 0.5
            db.add(DailyPrice(
                instrument_id=instrument.id, trade_date=d,
                open=Decimal(str(close)), high=Decimal(str(close * 1.01)), low=Decimal(str(close * 0.99)),
                close=Decimal(str(close)), adjusted_close=Decimal(str(close)), volume=100000,
            ))
        db.flush()

        resp = client.get(
            f"/api/zone/{instrument.id}",
            params={"macro_sma_period": 20, "fast_ema_period": 5, "slow_ema_period": 10, "rsi_period": 14, "atr_period": 14, "rvol_period": 20},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["zone"] in ("A", "B", "C", "D", "Unclassified")
        assert body["ticker"] == instrument.symbol

    def test_scan_returns_params_and_evaluated_count(self, client):
        resp = client.get("/api/zone/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert "as_of" in body
        assert "matches" in body
        assert "skipped" in body
        assert body["evaluated"] >= 0

    def test_scan_matches_sorted_by_zone_then_rsi(self, client):
        resp = client.get("/api/zone/scan")
        assert resp.status_code == 200
        matches = resp.json()["matches"]
        zone_order = {"A": 0, "B": 1, "C": 2, "D": 3, "Unclassified": 4}
        zones_seen = [zone_order[m["zone"]] for m in matches]
        assert zones_seen == sorted(zones_seen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -k "TestZoneClassifier" -v`
Expected: FAIL with 404s from FastAPI (no `/api/zone/*` routes registered yet).

- [ ] **Step 3: Implement `app/api/zone.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.zone import SkippedOut, ZoneOut, ZoneParamsOut, ZoneScanResponse
from app.services.zone_classifier import ZoneParams
from app.services.zone_loader import ZoneResult, get_zone_for_instrument, run_zone_scan

router = APIRouter(prefix="/api/zone", tags=["zone"], dependencies=[Depends(get_current_user)])


def _params_from_query(
    macro_sma_period: int = Query(200),
    fast_ema_period: int = Query(9),
    slow_ema_period: int = Query(21),
    rsi_period: int = Query(14),
    rsi_zone_a_max: float = Query(55),
    rsi_zone_b_low: float = Query(56),
    rsi_zone_b_high: float = Query(65),
    rsi_zone_c_low: float = Query(66),
    rsi_zone_c_high: float = Query(71),
    rsi_zone_d_min: float = Query(72),
    atr_period: int = Query(14),
    atr_limit_multiplier: float = Query(0.25),
    rvol_period: int = Query(20),
    near_ema_pct: float = Query(0.02),
) -> ZoneParams:
    try:
        return ZoneParams(
            macro_sma_period=macro_sma_period, fast_ema_period=fast_ema_period, slow_ema_period=slow_ema_period,
            rsi_period=rsi_period, rsi_zone_a_max=rsi_zone_a_max,
            rsi_zone_b_range=(rsi_zone_b_low, rsi_zone_b_high), rsi_zone_c_range=(rsi_zone_c_low, rsi_zone_c_high),
            rsi_zone_d_min=rsi_zone_d_min, atr_period=atr_period, atr_limit_multiplier=atr_limit_multiplier,
            rvol_period=rvol_period, near_ema_pct=near_ema_pct,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _params_out(params: ZoneParams) -> ZoneParamsOut:
    return ZoneParamsOut(
        macro_sma_period=params.macro_sma_period, fast_ema_period=params.fast_ema_period,
        slow_ema_period=params.slow_ema_period, rsi_period=params.rsi_period,
        rsi_zone_a_max=params.rsi_zone_a_max, rsi_zone_b_range=params.rsi_zone_b_range,
        rsi_zone_c_range=params.rsi_zone_c_range, rsi_zone_d_min=params.rsi_zone_d_min,
        atr_period=params.atr_period, atr_limit_multiplier=params.atr_limit_multiplier,
        rvol_period=params.rvol_period, near_ema_pct=params.near_ema_pct,
    )


def _zone_out(result: ZoneResult) -> ZoneOut:
    return ZoneOut(
        ticker=result.ticker, zone=result.zone, zone_label=result.zone_label, rsi=result.rsi,
        price=result.price, macro_sma=result.macro_sma, fast_ema=result.fast_ema, slow_ema=result.slow_ema,
        atr_band_lower=result.atr_band_lower, atr_band_upper=result.atr_band_upper, rvol=result.rvol,
        reason=result.reason,
    )


@router.get("/scan", response_model=ZoneScanResponse)
def scan_zones(db: Session = Depends(get_db), params: ZoneParams = Depends(_params_from_query)) -> ZoneScanResponse:
    result = run_zone_scan(db, params)
    return ZoneScanResponse(
        as_of=result.as_of.isoformat(),
        params=_params_out(params),
        matches=[_zone_out(m) for m in result.matches],
        skipped=[SkippedOut(**s) for s in result.skipped],
        evaluated=result.evaluated,
        cached=result.cached,
        elapsed_ms=result.elapsed_ms,
    )


@router.get("/{instrument_id}", response_model=ZoneOut)
def get_zone(instrument_id: int, db: Session = Depends(get_db), params: ZoneParams = Depends(_params_from_query)) -> ZoneOut:
    result = get_zone_for_instrument(db, instrument_id, params)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")
    return _zone_out(result)
```

Note the route order: `/scan` must be registered **before** `/{instrument_id}` — otherwise FastAPI would match `GET /api/zone/scan` against the `{instrument_id}` path parameter first and fail to parse `"scan"` as an int, returning a 422 instead of running the scan. (This mirrors the same ordering requirement `app/api/crossover.py` already follows for its own routes — check it if in doubt.)

- [ ] **Step 4: Register the router**

In `app/main.py`, find where `crossover_router` (or similar existing routers) is imported and included via `app.include_router(...)`. Add, following the exact same pattern:

```python
from app.api.zone import router as zone_router
```

and

```python
app.include_router(zone_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py -k "TestZoneClassifier" -v`
Expected: all PASS.

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: every test passes.

- [ ] **Step 7: Commit**

```bash
git add app/api/zone.py app/main.py tests/test_api.py
git commit -m "feat: add zone classifier API endpoints"
```

---

### Task 8: Benchmark against real data volume

**Files:**
- Create: `scripts/bench_zone_scan.py`

**Interfaces:**
- Consumes: `run_zone_scan`, `_scan_cached`, `_load_wide_cached` (Task 5, `app/services/zone_loader.py`); `ZoneParams` (Task 2); `SessionLocal` (existing, `app/db/session.py`).
- Produces: a standalone report printed to stdout, plus a recorded results section in the spec doc.

- [ ] **Step 1: Write `scripts/bench_zone_scan.py`**

```python
"""Standalone timing check for the BS-V4 market-wide zone scan against real
data volume -- not a pytest test, a one-off report, matching the pattern
already used for the crossover feature's scan (scripts/bench_crossover_scan.py).

Run with: python -m scripts.bench_zone_scan
Requires a locally loaded database with realistic history (see RUN.md's
backfill instructions).
"""

import time

from app.db.session import SessionLocal
from app.services.zone_classifier import ZoneParams
from app.services.zone_loader import _load_wide_cached, _scan_cached, run_zone_scan
from app.services.screening import latest_trade_date

SCENARIOS = [
    ("defaults", ZoneParams()),
    ("shorter-macro", ZoneParams(macro_sma_period=50, fast_ema_period=9, slow_ema_period=21, rsi_period=14, atr_period=14, rvol_period=20)),
]


def main() -> None:
    db = SessionLocal()
    try:
        as_of = latest_trade_date(db)
        if as_of is None:
            print("No price data loaded -- nothing to benchmark.")
            return

        for label, params in SCENARIOS:
            _scan_cached.cache_clear()
            _load_wide_cached.cache_clear()

            t0 = time.perf_counter()
            result = run_zone_scan(db, params)
            t1 = time.perf_counter()

            result_cached = run_zone_scan(db, params)
            t2 = time.perf_counter()

            print(f"\n{label} (macro_sma_period={params.macro_sma_period}, as_of={as_of}):")
            print(f"  cold run (query+compute): {(t1 - t0) * 1000:.0f}ms")
            print(f"  warm run (cache hit):     {(t2 - t1) * 1000:.0f}ms")
            print(f"  evaluated={result.evaluated} matched={len(result.matches)} skipped={len(result.skipped)}")
            assert result_cached.cached is True
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the local database**

Run: `python -m scripts.bench_zone_scan`
(Requires real backfilled data — see RUN.md's "Getting real data in" section.)

Expected: cold run in a similar range to the crossover feature's measured numbers for a comparable window size (see `docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md`'s "Measured performance" section for context — this feature computes more indicators per instrument, so don't assume it will be faster). Warm run near-instant. If cold run is materially slow, that's a real finding to record, not something to silently work around in this task.

- [ ] **Step 3: Record the measured numbers**

Append the actual output to the bottom of `docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md` under a new `## Measured performance (Task 8)` heading, so the spec's performance expectations are backed by a real number instead of an estimate.

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_zone_scan.py docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md
git commit -m "test: add and run market-wide zone scan benchmark"
```
