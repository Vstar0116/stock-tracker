"""DB-backed loading, indicator computation, and orchestration for the
BS-V4 Zone Classifier's single-instrument path. See zone_loader's scan
functions (Task 5) for the market-wide path.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs.compute_indicators import load_price_history
from app.models import Instrument
from app.services.crossover import STALE_TOLERANCE_DAYS
from app.services.crossover_loader import resolve_window
from app.services.indicators import atr, ema, rsi, sma, volume_sma
from app.services.screening import latest_trade_date
from app.services.zone_classifier import ZoneParams, classify_zone, classify_zones_wide


@dataclass(frozen=True)
class ZoneResult:
    instrument_id: int
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


def _insufficient_data(instrument_id: int, ticker: str, reason: str) -> ZoneResult:
    return ZoneResult(
        instrument_id=instrument_id, ticker=ticker, zone="Insufficient Data", zone_label="Insufficient Data",
        rsi=None, price=None, macro_sma=None, fast_ema=None, slow_ema=None,
        atr_band_lower=None, atr_band_upper=None, rvol=None, reason=reason,
    )


def get_zone_for_instrument(db: Session, instrument_id: int, params: ZoneParams) -> ZoneResult | None:
    """Returns None if instrument_id doesn't exist -- caller maps that to a 404."""
    instrument = db.execute(select(Instrument).where(Instrument.id == instrument_id)).scalars().first()
    if instrument is None:
        return None

    history = load_price_history(db, instrument_id)
    # Only rsi() needs a +1 bar (its internal diff() drops the first
    # observation before Wilder smoothing starts) -- sma/ema/atr/volume_sma
    # all become non-NaN at exactly `window` bars. See app/services/indicators.py.
    needed = max(params.macro_sma_period, params.slow_ema_period, params.atr_period, params.rvol_period, params.rsi_period + 1)
    if len(history) < needed:
        return _insufficient_data(instrument.id, instrument.symbol, f"needs {needed} bars of history, has {len(history)}")

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
        return _insufficient_data(instrument.id, instrument.symbol, "latest bar has NaN indicator values")

    zone, zone_label, reason = classify_zone(
        latest["rsi"], latest["price"], latest["macro_sma"], latest["fast_ema"], latest["slow_ema"], params,
    )
    rvol = latest["volume"] / latest["volume_sma"] if latest["volume_sma"] else None
    atr_band_lower = latest["macro_sma"] - 0.5 * latest["atr"] if zone == "A" else None
    atr_band_upper = latest["slow_ema"] + params.atr_limit_multiplier * latest["atr"] if zone == "B" else None

    return ZoneResult(
        instrument_id=instrument.id, ticker=instrument.symbol, zone=zone, zone_label=zone_label,
        rsi=latest["rsi"], price=latest["price"], macro_sma=latest["macro_sma"],
        fast_ema=latest["fast_ema"], slow_ema=latest["slow_ema"],
        atr_band_lower=atr_band_lower, atr_band_upper=atr_band_upper, rvol=rvol, reason=reason,
    )


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


# maxsize=1, not more: each entry is the whole active market pivoted to
# bars x instruments, so this cache is the app's single largest memory
# consumer and it lives in every gunicorn worker. `as_of` is the same for
# all users on a given trading day, so one entry still serves the common
# case (everyone on default params); a parameter sweep now costs a re-query
# instead of pinning several hundred MB on a 512MB instance.
@lru_cache(maxsize=1)
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
        {
            "instrument_id": iid,
            "ticker": symbols.get(iid, str(iid)),
            "reason": "insufficient history or NaN indicator value",
        }
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
            instrument_id=iid, ticker=symbols.get(iid, str(iid)), zone=zone, zone_label=zone_label,
            rsi=latest_rsi[iid], price=latest_price[iid], macro_sma=latest_macro_sma[iid],
            fast_ema=latest_fast_ema[iid], slow_ema=latest_slow_ema[iid],
            atr_band_lower=atr_band_lower, atr_band_upper=atr_band_upper, rvol=rvol, reason=reason,
        ))

    zone_order = {"A": 0, "B": 1, "C": 2, "D": 3, "Unclassified": 4}
    matches.sort(key=lambda m: (zone_order[m.zone], m.rsi))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return ScanResult(as_of=as_of, matches=matches, skipped=skipped, evaluated=total_active, cached=False, elapsed_ms=elapsed_ms)


def run_zone_scan(db: Session, params: ZoneParams, instrument_ids: frozenset[int] | None = None) -> ScanResult:
    """`lru_cache` doesn't expose "was this specific key already cached" via
    cache_info() (only aggregate hit/miss counts across all keys) -- and a
    cache-hit result is the exact same frozen ScanResult object from when it
    was first computed, with cached=False still baked into it. Comparing the
    hit counter before/after this one call is what tells us which case we're
    in, so we can override cached=True on the returned object after the fact.

    `instrument_ids`, when given, scopes the result to one watchlist. The
    underlying whole-market computation stays on its (params, as_of) cache
    key regardless -- it's the same scan for every watchlist and every user,
    so filtering happens after the cached call rather than threading the
    watchlist into the cache key and recomputing per watchlist.
    """
    as_of = latest_trade_date(db)
    if as_of is None:
        raise ValueError("no price data loaded yet")

    hits_before = _scan_cached.cache_info().hits
    result = _scan_cached(params, as_of)
    was_cache_hit = _scan_cached.cache_info().hits > hits_before
    result = dataclasses.replace(result, cached=was_cache_hit) if was_cache_hit else result

    if instrument_ids is None:
        return result

    matches = [m for m in result.matches if m.instrument_id in instrument_ids]
    skipped = [s for s in result.skipped if s["instrument_id"] in instrument_ids]
    return dataclasses.replace(result, matches=matches, skipped=skipped, evaluated=len(matches) + len(skipped))
