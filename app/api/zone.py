"""BS-V4 Zone Classifier: view on one instrument (instant) and scan the whole
market (Task 5). Additive to the existing indicator/screener/crossover
features -- see docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md.
"""

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
