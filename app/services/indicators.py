"""Technical indicator calculations. Pure functions: pandas Series/DataFrame in,
pandas Series/DataFrame out, no I/O.

Rules these functions all follow (see CLAUDE.md architecture principle #4 and
the task that created this file):
- Always computed from adjusted_close, never raw close.
- NULL (NaN) until there is strictly enough history for the indicator's
  defined window -- a partial-window average is not an approximation of the
  real indicator, it's a different, wrong number. Never emit one.
- Price/volume inputs are treated as float64 for this module. daily_prices
  stores exact Decimal/NUMERIC; these are derived analytical values, and
  float math for indicators is the universal industry convention (every
  major charting platform does this) -- it never touches the stored price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SMA_WINDOWS = (20, 50, 100, 200)
EMA_WINDOWS = (20, 50)
RSI_WINDOW = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ATR_WINDOW = 14
VOLUME_SMA_WINDOW = 20
WEEKS_52_WINDOW = 252  # trading days/year convention, not a 365-calendar-day window


def sma(prices: pd.Series, window: int) -> pd.Series:
    """Simple moving average. NaN until `window` real observations exist in
    the window (pandas rolling min_periods=window already enforces this)."""
    return prices.rolling(window=window, min_periods=window).mean()


def ema(prices: pd.Series, window: int) -> pd.Series:
    """Exponential moving average: standard recursive definition with
    alpha = 2 / (window + 1) (pandas `span=window`, adjust=False).

    NaN until `window` real (non-NaN) observations have been seen in the
    input -- tracked via a running non-NaN count rather than raw position, so
    this is safe to call on a derived series that already has its own
    leading NaNs (e.g. MACD's signal line is ema() applied to the MACD line,
    which itself starts with `slow - 1` NaNs).
    """
    result = prices.ewm(span=window, adjust=False).mean()
    have_enough = prices.notna().expanding().sum() >= window
    return result.where(have_enough)


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


def rsi(prices: pd.Series, window: int = RSI_WINDOW) -> pd.Series:
    """Relative Strength Index using Wilder's original smoothing (not a plain
    EMA -- see _wilder_smoothing). Formula:

        delta = price.diff()
        avg_gain = wilder_smoothing(max(delta, 0), window)
        avg_loss = wilder_smoothing(max(-delta, 0), window)
        RS = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)

    RSI is defined as 100 when avg_loss is exactly 0 (no losses in the
    window), rather than the NaN/inf a literal division would produce.

    Needs `window + 1` price bars (window deltas) before the first non-NULL
    value: delta.diff() drops the first bar (nothing to compare it to), so
    Wilder's seed window starts one bar later than for ATR.
    """
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = _wilder_smoothing(gain.iloc[1:], window).reindex(prices.index)
    avg_loss = _wilder_smoothing(loss.iloc[1:], window).reindex(prices.index)

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    result = result.where(avg_loss != 0, other=100.0)
    result = result.where(avg_gain.notna() & avg_loss.notna())
    return result


def macd(
    prices: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL
) -> pd.DataFrame:
    """MACD: line = EMA(fast) - EMA(slow); signal = EMA(line, signal);
    histogram = line - signal. Standard defaults (12, 26, 9).

    line needs `slow` bars; signal (and therefore histogram) needs
    `slow + signal - 1` bars, since it's an EMA of a series that itself
    starts `slow - 1` bars in.
    """
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    line = ema_fast - ema_slow
    signal_line = ema(line, signal)
    histogram = line - signal_line
    return pd.DataFrame({"macd": line, "macd_signal": signal_line, "macd_histogram": histogram})


def atr(high: pd.Series | pd.DataFrame, low: pd.Series | pd.DataFrame, close: pd.Series | pd.DataFrame, adjusted_close: pd.Series | pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series | pd.DataFrame:
    """Average True Range using Wilder's original smoothing.

    daily_prices only stores adjusted_close (no adjusted high/low), so
    high/low are scaled by each row's own adjusted_close/close ratio before
    computing True Range. Without this, a split/bonus would show up as a
    fake multi-hundred-percent ATR spike on the ex_date, because raw
    high/low would jump to a different scale than the already-adjusted
    close it's being compared against.

        True Range = max(high-low, |high - prev_close|, |low - prev_close|)
        ATR = Wilder's smoothed moving average of True Range over `window` bars

    Needs `window` price bars (True Range on the very first bar falls back
    to just high-low, since there's no previous close to gap against).

    Accepts both Series (one instrument) and DataFrame (whole universe at once).
    """
    scale = adjusted_close / close
    adj_high = high * scale
    adj_low = low * scale
    prev_adj_close = adjusted_close.shift(1)

    tr_parts = [adj_high - adj_low, (adj_high - prev_adj_close).abs(), (adj_low - prev_adj_close).abs()]

    # Element-wise max across the three TR components: np.fmax skips NaN (correct
    # for first-bar fallback where prev_adj_close is NaN) unlike np.maximum which
    # would propagate it. Single vectorized expression works for both Series and
    # DataFrame with no Python-level loops.
    true_range_values = np.fmax(np.fmax(tr_parts[0].values, tr_parts[1].values), tr_parts[2].values)

    # Reconstruct as the same type as input
    if isinstance(tr_parts[0], pd.DataFrame):
        true_range = pd.DataFrame(true_range_values, index=tr_parts[0].index, columns=tr_parts[0].columns)
    else:
        true_range = pd.Series(true_range_values, index=tr_parts[0].index)

    return _wilder_smoothing(true_range, window)


def volume_sma(volume: pd.Series, window: int = VOLUME_SMA_WINDOW) -> pd.Series:
    """Simple moving average of daily volume. Same min-history rule as price SMA."""
    return volume.rolling(window=window, min_periods=window).mean()


def rolling_52w_high(prices: pd.Series, window: int = WEEKS_52_WINDOW) -> pd.Series:
    """Trailing high over the last `window` trading bars (252, the standard
    trading-days-per-year approximation of "52 weeks" -- not a 365-calendar-day
    window)."""
    return prices.rolling(window=window, min_periods=window).max()


def rolling_52w_low(prices: pd.Series, window: int = WEEKS_52_WINDOW) -> pd.Series:
    return prices.rolling(window=window, min_periods=window).min()


def compute_all_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute every indicator for one instrument's full price history.

    `prices` must be indexed by trade_date (ascending) with columns open,
    high, low, close, adjusted_close, volume for ONE instrument. Returns a
    DataFrame with the same index and one column per `indicators` table
    column (see app/models/prices.py::Indicator).
    """
    price = prices["adjusted_close"].astype(float)
    macd_df = macd(price)

    return pd.DataFrame(
        {
            "sma_20": sma(price, 20),
            "sma_50": sma(price, 50),
            "sma_100": sma(price, 100),
            "sma_200": sma(price, 200),
            "ema_20": ema(price, 20),
            "ema_50": ema(price, 50),
            "rsi_14": rsi(price, RSI_WINDOW),
            "macd": macd_df["macd"],
            "macd_signal": macd_df["macd_signal"],
            "macd_histogram": macd_df["macd_histogram"],
            "atr_14": atr(
                prices["high"].astype(float),
                prices["low"].astype(float),
                prices["close"].astype(float),
                price,
                ATR_WINDOW,
            ),
            "volume_sma_20": volume_sma(prices["volume"].astype(float), VOLUME_SMA_WINDOW),
            "high_52w": rolling_52w_high(price, WEEKS_52_WINDOW),
            "low_52w": rolling_52w_low(price, WEEKS_52_WINDOW),
        },
        index=prices.index,
    )
