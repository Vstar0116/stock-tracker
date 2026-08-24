"""Tests for app/services/crossover.py using hand-verifiable inputs.

Pure pandas functions -- no database needed.
Run with: pytest tests/test_crossover.py -v
"""

import numpy as np
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
        # When fast and slow MAs are equal (flat price, all SMA values are the price),
        # diff is always 0, so no signal is emitted. Equal counts as not-yet-crossed.
        prices = _prices([50.0] * 7)
        result = compute_crossover(prices, fast=1, slow=2, ma_type="sma")
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


class TestScanLastBarParity:
    def test_matches_per_instrument_compute_crossover(self):
        # Build a wide frame of several independent instruments, each with
        # its own randomized-but-seeded walk, and confirm scan_last_bar's
        # vectorized result agrees with running compute_crossover on each
        # column separately -- this is what keeps the instant endpoint and
        # the market-wide scan from ever disagreeing.
        from app.services.crossover import scan_last_bar

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
                    if pd.isna(expected):
                        assert instrument_id not in vectorized.index
                    else:
                        assert vectorized[instrument_id] == expected

    def test_drops_instruments_with_no_signal(self):
        from app.services.crossover import scan_last_bar

        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        wide = pd.DataFrame({1: [50.0] * 10, 2: [10, 10, 10, 10, 10, 10, 10, 10, 30, 30]}, index=dates)
        result = scan_last_bar(wide, fast=2, slow=3, ma_type="sma")
        assert 1 not in result.index  # flat series, never crosses
