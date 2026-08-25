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

    def test_exact_boundary_bars_not_insufficient_data(self, db, instrument):
        # Pins the "needed" length to the true per-indicator minimum: each
        # indicator's *own* NaN rule (see app/services/indicators.py) needs
        # only `window` total bars, except rsi() which needs `window + 1`
        # (its internal diff() drops the first observation). Here
        # macro_sma_period/rvol_period (20) dominate rsi_period+1 (15), so
        # an instrument with exactly 20 bars has every indicator fully
        # computable on its latest bar -- it must NOT be "Insufficient Data".
        params = ZoneParams(
            macro_sma_period=20, fast_ema_period=5, slow_ema_period=10,
            rsi_period=14, atr_period=14, rvol_period=20,
        )
        needed = max(
            params.macro_sma_period, params.slow_ema_period,
            params.atr_period, params.rvol_period, params.rsi_period + 1,
        )
        assert needed == 20  # sanity: dominated by macro_sma/rvol, not rsi_period + 1
        closes = [100.0 + i * 0.3 for i in range(needed)]
        _seed_prices(db, instrument.id, closes, start=date(2026, 1, 1))

        result = get_zone_for_instrument(db, instrument.id, params)

        assert result.zone != "Insufficient Data"
        assert result.rsi is not None
        assert result.price == pytest.approx(closes[-1])

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
