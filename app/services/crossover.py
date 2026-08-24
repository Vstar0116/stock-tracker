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
