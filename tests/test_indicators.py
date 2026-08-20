"""Tests for app/services/indicators.py using hand-verifiable inputs.

Pure pandas functions -- no database needed.
Run with: pytest tests/test_indicators.py -v
"""

import numpy as np
import pandas as pd
import pytest

from app.services.indicators import atr, ema, macd, rolling_52w_high, rolling_52w_low, rsi, sma, volume_sma


class TestSMA:
    def test_known_sequence(self):
        # SMA(3) of [1,2,3,4,5]: NaN, NaN, mean(1,2,3)=2, mean(2,3,4)=3, mean(3,4,5)=4
        prices = pd.Series([1, 2, 3, 4, 5])
        result = sma(prices, 3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].tolist() == [2.0, 3.0, 4.0]

    def test_null_when_history_shorter_than_window(self):
        # The user's own example: an SMA-200 must be NULL with only 30 bars,
        # never a value computed from fewer bars.
        prices = pd.Series(range(30), dtype=float)
        result = sma(prices, 200)
        assert result.isna().all()


class TestEMA:
    def test_known_sequence_span3(self):
        # alpha = 2/(3+1) = 0.5, adjust=False: seed=10, then 0.5*x + 0.5*prev
        prices = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = ema(prices, 3)
        assert result.iloc[:2].isna().all()  # needs 3 bars
        expected = [22.5, 31.25, 40.625]  # hand-computed recursive EMA
        assert result.iloc[2:].tolist() == pytest.approx(expected)

    def test_composed_series_with_leading_nans_needs_its_own_window(self):
        # EMA applied to a series that ALREADY has leading NaNs (like MACD's
        # signal line applied to the MACD line) must still require `window`
        # REAL values after the NaN region, not just `window` positions from
        # the start of the series.
        values = pd.Series([np.nan, np.nan, np.nan] + [5.0] * 10)
        result = ema(values, 5)
        # first 3 positions are the source NaNs; need 5 more real values ->
        # first valid output at position 3+5-1 = 7
        assert result.iloc[:7].isna().all()
        assert not pd.isna(result.iloc[7])


class TestRSI:
    def test_known_sequence_gives_rsi_50(self):
        # 14 deltas, exactly 7 up-days of +1 and 7 down-days of -1 ->
        # avg_gain = avg_loss = 0.5 -> RS = 1 -> RSI = 100 - 100/2 = 50 exactly.
        prices = pd.Series([100, 101, 102, 101, 100, 99, 100, 101, 102, 103, 104, 103, 102, 101, 100], dtype=float)
        result = rsi(prices, 14)
        assert result.iloc[:14].isna().all()  # needs window+1 = 15 bars
        assert result.iloc[14] == pytest.approx(50.0)

    def test_no_losses_gives_rsi_100_not_nan_or_inf(self):
        prices = pd.Series([100.0] + [101.0 + i for i in range(14)])  # strictly rising
        result = rsi(prices, 14)
        assert result.iloc[14] == pytest.approx(100.0)


class TestMACD:
    def test_constant_price_gives_zero_line_signal_histogram(self):
        # EMA of a constant series is exactly that constant at every position,
        # once past its own min-history threshold -- so line = signal = 0 for
        # a flat price, with no rounding ambiguity.
        prices = pd.Series([100.0] * 40)
        result = macd(prices)

        assert result["macd"].iloc[:25].isna().all()  # needs `slow`=26 bars
        assert result["macd"].iloc[25:].tolist() == pytest.approx([0.0] * 15)

        assert result["macd_signal"].iloc[:33].isna().all()  # needs slow+signal-1=34 bars
        assert result["macd_signal"].iloc[33:].tolist() == pytest.approx([0.0] * 7)
        assert result["macd_histogram"].iloc[33:].tolist() == pytest.approx([0.0] * 7)


class TestATR:
    def test_constant_true_range(self):
        # high=101/low=99/close=100 every day -> True Range is exactly 2 every
        # day (day 1 falls back to high-low since there's no prior close).
        n = 15
        high = pd.Series([101.0] * n)
        low = pd.Series([99.0] * n)
        close = pd.Series([100.0] * n)
        adjusted_close = close.copy()  # no corporate action -- scale factor is 1

        result = atr(high, low, close, adjusted_close, window=14)

        assert result.iloc[:13].isna().all()  # needs 14 bars
        assert result.iloc[13:].tolist() == pytest.approx([2.0, 2.0])

    def test_split_does_not_produce_a_fake_spike(self):
        # A 2:1 split on day 5 (raw high/low/close halve, adjusted_close is
        # already continuous). Without scaling high/low by adjusted_close/close,
        # True Range on the split day would show a huge fake spike.
        raw_close = pd.Series([100.0] * 5 + [50.0] * 10)
        raw_high = raw_close + 1
        raw_low = raw_close - 1
        adjusted_close = pd.Series([50.0] * 5 + [50.0] * 10)  # pre-split rows halved, post-split unchanged

        result = atr(raw_high, raw_low, raw_close, adjusted_close, window=14)
        # every scaled True Range is still ~2 (1 pre-split-scale, 1 post) -- no spike
        assert result.dropna().max() < 3.0


class TestVolumeSMA:
    def test_known_sequence(self):
        volume = pd.Series([100, 200, 300, 400, 500], dtype=float)
        result = volume_sma(volume, 3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].tolist() == [200.0, 300.0, 400.0]


class Test52WeekHighLow:
    def test_known_sequence(self):
        # Strictly increasing 1..260: in a 252-bar window, the max is always
        # today's value and the min is always the value 251 bars back.
        prices = pd.Series(range(1, 261), dtype=float)
        high = rolling_52w_high(prices, window=252)
        low = rolling_52w_low(prices, window=252)

        assert high.iloc[:251].isna().all()
        assert low.iloc[:251].isna().all()
        assert high.iloc[251] == 252.0
        assert low.iloc[251] == 1.0
        assert high.iloc[259] == 260.0
        assert low.iloc[259] == 9.0
