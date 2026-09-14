"""Per-user dashboard: whole-market breadth (reused as-is from
market_snapshot, already cached there), movers among the requesting user's
own watchlists, that user's screens that fired today, and a whole-market
sector heatmap.

The heatmap groups by `instruments.industry`, not `.sector` -- `.sector` is
the curated BS-GARP tag populated for a ~49-instrument subset (see
app/jobs/seed_bsgarp_fundamentals.py), while `.industry` is NSE's own broad
taxonomy populated for ~750 instruments by
app/jobs/ingest_sector_classification.py. Grouping by `.sector` here would
show almost nothing. "sector" in the API/UI is the redesign's plain-English
word for this breakdown; the column backing it is `industry`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from sqlalchemy import func, select, text, true
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models import Alert, DailyPrice, Instrument, Screen, Watchlist, WatchlistItem
from app.schemas.dashboard import DashboardMoverOut, DashboardOut, FiredScreenOut, SectorHeatOut
from app.services.market_snapshot import build_snapshot
from app.services.screening import latest_trade_date

TOP_MOVERS_N = 8
TOP_FIRED_SCREENS_N = 5
MIN_HEATMAP_INSTRUMENTS = 3  # drop sectors too thin to average meaningfully


def _connect():
    """Same indirection as market_snapshot._connect -- lets tests substitute
    the SAVEPOINT-backed test connection."""
    return engine.connect()


@dataclass
class _SectorHeat:
    sector: str
    change_pct: float
    count: int


_HEATMAP_SQL = text(
    """
    WITH last2 AS (
        SELECT p.instrument_id, i.industry, p.adjusted_close,
               ROW_NUMBER() OVER (PARTITION BY p.instrument_id ORDER BY p.trade_date DESC) AS rn
        FROM daily_prices p
        JOIN instruments i ON i.id = p.instrument_id
        WHERE i.is_active AND i.industry IS NOT NULL
              AND p.trade_date <= :as_of AND p.trade_date > :as_of - INTERVAL '10 days'
    ),
    piv AS (
        SELECT instrument_id, industry,
               MAX(adjusted_close) FILTER (WHERE rn = 1) AS close_today,
               MAX(adjusted_close) FILTER (WHERE rn = 2) AS close_prev
        FROM last2
        WHERE rn <= 2
        GROUP BY instrument_id, industry
    )
    SELECT industry,
           AVG((close_today - close_prev) / NULLIF(close_prev, 0) * 100) AS avg_pct,
           COUNT(*) AS cnt
    FROM piv
    WHERE close_today IS NOT NULL AND close_prev IS NOT NULL AND close_prev != 0
    GROUP BY industry
    HAVING COUNT(*) >= :min_count
    ORDER BY avg_pct DESC
    """
)


@lru_cache(maxsize=1)
def _heatmap_cached(as_of: date) -> list[_SectorHeat]:
    with _connect() as conn:
        rows = conn.execute(_HEATMAP_SQL, {"as_of": as_of, "min_count": MIN_HEATMAP_INSTRUMENTS}).all()
    return [_SectorHeat(sector=r.industry, change_pct=float(r.avg_pct), count=r.cnt) for r in rows]


def _user_movers(db: Session, user_id: int) -> list[DashboardMoverOut]:
    # Same correlated-LATERAL shape as view_watchlist in app/api/watchlists.py,
    # minus the indicators lateral (dashboard movers only need price/day-change).
    latest_price = (
        select(DailyPrice.trade_date, DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == Instrument.id)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
        .lateral("dm_price")
    )
    prev_price = (
        select(DailyPrice.adjusted_close)
        .where(DailyPrice.instrument_id == Instrument.id, DailyPrice.trade_date < latest_price.c.trade_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
        .lateral("dm_prev_price")
    )
    stmt = (
        select(
            Instrument.id,
            Instrument.symbol,
            Instrument.exchange,
            Instrument.sector,
            latest_price.c.adjusted_close.label("close"),
            prev_price.c.adjusted_close.label("prev_close"),
        )
        .select_from(WatchlistItem)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
        .outerjoin(latest_price, true())
        .outerjoin(prev_price, true())
        .where(Watchlist.user_id == user_id)
    )

    by_instrument: dict[int, DashboardMoverOut] = {}
    for row in db.execute(stmt).all():
        if row.close is None or row.prev_close is None or row.prev_close == 0:
            continue
        close, prev_close = float(row.close), float(row.prev_close)
        pct = (close - prev_close) / prev_close * 100
        # Same instrument can sit in more than one of the user's watchlists --
        # keep one row per instrument.
        by_instrument[row.id] = DashboardMoverOut(
            instrument_id=row.id, symbol=row.symbol, exchange=row.exchange, sector=row.sector, close=close, change_pct=pct
        )
    return sorted(by_instrument.values(), key=lambda m: abs(m.change_pct), reverse=True)[:TOP_MOVERS_N]


def _fired_screens(db: Session, user_id: int, as_of: date) -> list[FiredScreenOut]:
    rows = db.execute(
        select(Alert.screen_id, Screen.name, func.count().label("cnt"))
        .join(Screen, Screen.id == Alert.screen_id)
        .where(Screen.user_id == user_id, Alert.trade_date == as_of)
        .group_by(Alert.screen_id, Screen.name)
        .order_by(func.count().desc())
        .limit(TOP_FIRED_SCREENS_N)
    ).all()
    return [FiredScreenOut(screen_id=r.screen_id, name=r.name, count=r.cnt) for r in rows]


def build_dashboard(db: Session, user_id: int) -> DashboardOut:
    snap = build_snapshot(db)
    watchlist_count = db.execute(select(func.count()).select_from(Watchlist).where(Watchlist.user_id == user_id)).scalar_one()
    heatmap = _heatmap_cached(snap.as_of)

    return DashboardOut(
        as_of=snap.as_of,
        watchlist_count=watchlist_count,
        instrument_count=snap.instrument_count,
        up_count=snap.up_count,
        down_count=snap.down_count,
        moved_2pct_count=snap.moved_2pct_count,
        golden_cross_count=snap.golden_cross_count,
        volume_breakout_count=snap.volume_breakout_count,
        alerts_today=snap.alerts_today,
        ticker=snap.ticker,
        movers=_user_movers(db, user_id),
        fired_screens=_fired_screens(db, user_id, snap.as_of),
        heatmap=[SectorHeatOut(sector=h.sector, change_pct=h.change_pct, count=h.count) for h in heatmap],
    )
