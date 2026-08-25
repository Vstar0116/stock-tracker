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
    # Only rsi() needs a +1 bar (its internal diff() drops the first
    # observation before Wilder smoothing starts) -- sma/ema/atr/volume_sma
    # all become non-NaN at exactly `window` bars. See app/services/indicators.py.
    needed = max(params.macro_sma_period, params.slow_ema_period, params.atr_period, params.rvol_period, params.rsi_period + 1)
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
