"""Watchlist CRUD + the "view" endpoint: every stock in a watchlist with its
latest price, latest indicators, and a computed trend_state -- built as ONE
query using LATERAL joins (each correlated to the outer instrument row, so
Postgres uses the (instrument_id, trade_date DESC) indexes on daily_prices
and indicators to seek straight to the latest row per stock instead of
scanning either table, and the app never issues a per-stock follow-up query).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, get_owned_watchlist, pagination
from app.db.session import get_db
from app.models import DailyPrice, Indicator, Instrument, User, Watchlist, WatchlistItem
from app.schemas.common import Page
from app.schemas.instrument import IndicatorOut
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemOut,
    WatchlistOut,
    WatchlistUpdate,
    WatchlistViewRow,
)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


def _trend_state(close: float | None, sma_50: float | None, sma_200: float | None) -> str:
    if close is None or sma_50 is None or sma_200 is None:
        return "unknown"
    if close > sma_50 > sma_200:
        return "uptrend"
    if close < sma_50 < sma_200:
        return "downtrend"
    return "neutral"


@router.get("", response_model=Page[WatchlistOut])
def list_watchlists(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination),
) -> Page[WatchlistOut]:
    total = db.execute(
        select(func.count()).select_from(Watchlist).where(Watchlist.user_id == current_user.id)
    ).scalar_one()
    rows = (
        db.execute(
            select(Watchlist)
            .where(Watchlist.user_id == current_user.id)
            .order_by(Watchlist.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return Page(items=[WatchlistOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    payload: WatchlistCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> WatchlistOut:
    watchlist = Watchlist(user_id=current_user.id, name=payload.name)
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    return WatchlistOut.model_validate(watchlist)


@router.patch("/{watchlist_id}", response_model=WatchlistOut)
def update_watchlist(
    payload: WatchlistUpdate,
    watchlist: Watchlist = Depends(get_owned_watchlist),
    db: Session = Depends(get_db),
) -> WatchlistOut:
    watchlist.name = payload.name
    db.commit()
    db.refresh(watchlist)
    return WatchlistOut.model_validate(watchlist)


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(watchlist: Watchlist = Depends(get_owned_watchlist), db: Session = Depends(get_db)) -> None:
    db.delete(watchlist)
    db.commit()


@router.post("/{watchlist_id}/items", response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED)
def add_item(
    payload: WatchlistItemCreate,
    watchlist: Watchlist = Depends(get_owned_watchlist),
    db: Session = Depends(get_db),
) -> WatchlistItemOut:
    if db.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    existing = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id, WatchlistItem.instrument_id == payload.instrument_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "instrument already in this watchlist")

    item = WatchlistItem(watchlist_id=watchlist.id, instrument_id=payload.instrument_id, notes=payload.notes)
    db.add(item)
    db.commit()
    db.refresh(item)
    return WatchlistItemOut.model_validate(item)


@router.delete("/{watchlist_id}/items/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(
    instrument_id: int,
    watchlist: Watchlist = Depends(get_owned_watchlist),
    db: Session = Depends(get_db),
) -> None:
    item = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id, WatchlistItem.instrument_id == instrument_id
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not in this watchlist")
    db.delete(item)
    db.commit()


@router.get("/{watchlist_id}/view", response_model=Page[WatchlistViewRow])
def view_watchlist(
    watchlist: Watchlist = Depends(get_owned_watchlist),
    db: Session = Depends(get_db),
    page: Pagination = Depends(pagination),
) -> Page[WatchlistViewRow]:
    total = db.execute(
        select(func.count()).select_from(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist.id)
    ).scalar_one()

    latest_price = (
        select(DailyPrice.trade_date, DailyPrice.adjusted_close, DailyPrice.volume)
        .where(DailyPrice.instrument_id == Instrument.id)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
        .lateral("wv_price")
    )
    # Previous trading day's close, for day-change % -- correlated to both
    # the outer instrument row and latest_price's own trade_date, so it
    # still uses the (instrument_id, trade_date DESC) index to seek.
    prev_price = (
        select(DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == Instrument.id, DailyPrice.trade_date < latest_price.c.trade_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
        .lateral("wv_prev_price")
    )
    latest_indicator = (
        select(
            Indicator.trade_date,
            Indicator.sma_20,
            Indicator.sma_50,
            Indicator.sma_100,
            Indicator.sma_200,
            Indicator.ema_20,
            Indicator.ema_50,
            Indicator.rsi_14,
            Indicator.macd,
            Indicator.macd_signal,
            Indicator.macd_histogram,
            Indicator.atr_14,
            Indicator.volume_sma_20,
            Indicator.high_52w,
            Indicator.low_52w,
        )
        .where(Indicator.instrument_id == Instrument.id)
        .order_by(Indicator.trade_date.desc())
        .limit(1)
        .lateral("wv_indicator")
    )

    stmt = (
        select(
            Instrument.id.label("instrument_id"),
            Instrument.symbol,
            Instrument.exchange,
            Instrument.company_name,
            Instrument.sector,
            WatchlistItem.notes,
            WatchlistItem.added_at,
            latest_price.c.trade_date,
            latest_price.c.adjusted_close.label("close"),
            prev_price.c.adjusted_close.label("prev_close"),
            latest_price.c.volume,
            latest_indicator.c.trade_date.label("indicator_trade_date"),
            latest_indicator.c.sma_20,
            latest_indicator.c.sma_50,
            latest_indicator.c.sma_100,
            latest_indicator.c.sma_200,
            latest_indicator.c.ema_20,
            latest_indicator.c.ema_50,
            latest_indicator.c.rsi_14,
            latest_indicator.c.macd,
            latest_indicator.c.macd_signal,
            latest_indicator.c.macd_histogram,
            latest_indicator.c.atr_14,
            latest_indicator.c.volume_sma_20,
            latest_indicator.c.high_52w,
            latest_indicator.c.low_52w,
        )
        .select_from(WatchlistItem)
        .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
        .outerjoin(latest_price, true())
        .outerjoin(prev_price, true())
        .outerjoin(latest_indicator, true())
        .where(WatchlistItem.watchlist_id == watchlist.id)
        .order_by(Instrument.symbol)
        .limit(page.limit)
        .offset(page.offset)
    )

    items = []
    for row in db.execute(stmt).mappings().all():
        indicators = None
        if row["indicator_trade_date"] is not None:
            indicators = IndicatorOut(
                trade_date=row["indicator_trade_date"],
                sma_20=row["sma_20"],
                sma_50=row["sma_50"],
                sma_100=row["sma_100"],
                sma_200=row["sma_200"],
                ema_20=row["ema_20"],
                ema_50=row["ema_50"],
                rsi_14=row["rsi_14"],
                macd=row["macd"],
                macd_signal=row["macd_signal"],
                macd_histogram=row["macd_histogram"],
                atr_14=row["atr_14"],
                volume_sma_20=row["volume_sma_20"],
                high_52w=row["high_52w"],
                low_52w=row["low_52w"],
            )
        close = float(row["close"]) if row["close"] is not None else None
        prev_close = float(row["prev_close"]) if row["prev_close"] is not None else None
        day_change_pct = (close - prev_close) / prev_close * 100 if close is not None and prev_close else None
        sma_50 = float(row["sma_50"]) if row["sma_50"] is not None else None
        sma_200 = float(row["sma_200"]) if row["sma_200"] is not None else None
        items.append(
            WatchlistViewRow(
                instrument_id=row["instrument_id"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                company_name=row["company_name"],
                sector=row["sector"],
                trade_date=row["trade_date"],
                close=close,
                day_change_pct=day_change_pct,
                volume=row["volume"],
                indicators=indicators,
                trend_state=_trend_state(close, sma_50, sma_200),
                notes=row["notes"],
                added_at=row["added_at"],
            )
        )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
