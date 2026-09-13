"""Real, whole-market numbers for the public landing page (app/api/public.py).

Deliberately reads only aggregate/whole-market facts -- instrument counts,
today-vs-yesterday price moves, golden crosses off the already-computed
Indicator.sma_200, volume breakouts off Indicator.volume_sma_20 -- nothing
here is scoped to a user, watchlist, or saved screen. `alerts_today` is the
one exception, and it's a bare count with no screen name or symbol attached,
so it can't identify whose screen fired.

The heavy aggregation is cached per trade_date, same pattern as
app/services/crossover_loader.py: daily_prices only changes once a day (the
evening pipeline), so `as_of` moving is the only thing that should ever
invalidate this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.schemas.public import MarketMoverOut, MarketSnapshotOut
from app.services.screening import latest_trade_date

# Well-known large caps for the ticker rail -- fixed, not "top by market
# cap" (market cap isn't ingested for the whole market), so the rail always
# resolves the same familiar names in a stable order.
TICKER_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "SUNPHARMA", "LT",
    "ITC", "TATAMOTORS", "BHARTIARTL", "JSWSTEEL", "TITAN", "AXISBANK",
]

MOVED_PCT_THRESHOLD = 2.0
VOLUME_BREAKOUT_RATIO = 3
TOP_N = 6

# ponytail: sub-Rs10 illiquid/penny instruments routinely show +-30-40% "moves"
# that are usually a thin order book or an unadjusted corporate action, not
# something worth leading a landing page with -- filters displayed lists
# only (top_movers/golden_cross/volume_breakout), never the whole-market
# up_count/down_count/moved_2pct_count breadth counts. Raise this if genuine
# small-caps keep getting excluded.
MIN_DISPLAY_PRICE = 10.0


def _connect():
    """Indirection point so tests can substitute the SAVEPOINT-backed test
    connection, same reasoning as crossover_loader._connect."""
    return engine.connect()


@dataclass
class _Row:
    symbol: str
    exchange: str
    close_today: float | None
    close_prev: float | None
    sma200_today: float | None
    sma200_prev: float | None
    volume_today: float | None
    volume_sma20_today: float | None


@dataclass
class _RawSnapshot:
    instrument_count: int
    alerts_today: int
    up_count: int = 0
    down_count: int = 0
    moved_2pct_count: int = 0
    golden_cross_count: int = 0
    volume_breakout_count: int = 0
    ticker: list[MarketMoverOut] = field(default_factory=list)
    top_movers: list[MarketMoverOut] = field(default_factory=list)
    golden_cross: list[MarketMoverOut] = field(default_factory=list)
    volume_breakout: list[MarketMoverOut] = field(default_factory=list)


_PIVOT_SQL = text(
    """
    WITH last2 AS (
        SELECT p.instrument_id, i.symbol, i.exchange, p.adjusted_close, p.volume,
               ind.sma_200, ind.volume_sma_20,
               ROW_NUMBER() OVER (PARTITION BY p.instrument_id ORDER BY p.trade_date DESC) AS rn
        FROM daily_prices p
        JOIN instruments i ON i.id = p.instrument_id
        LEFT JOIN indicators ind ON ind.instrument_id = p.instrument_id AND ind.trade_date = p.trade_date
        WHERE i.is_active AND p.trade_date <= :as_of AND p.trade_date > :as_of - INTERVAL '10 days'
    )
    SELECT symbol, exchange,
           MAX(adjusted_close) FILTER (WHERE rn = 1) AS close_today,
           MAX(adjusted_close) FILTER (WHERE rn = 2) AS close_prev,
           MAX(sma_200)        FILTER (WHERE rn = 1) AS sma200_today,
           MAX(sma_200)        FILTER (WHERE rn = 2) AS sma200_prev,
           MAX(volume)         FILTER (WHERE rn = 1) AS volume_today,
           MAX(volume_sma_20)  FILTER (WHERE rn = 1) AS volume_sma20_today
    FROM last2
    WHERE rn <= 2
    GROUP BY instrument_id, symbol, exchange
    """
)


@lru_cache(maxsize=1)
def _build_cached(as_of: date) -> _RawSnapshot:
    with _connect() as conn:
        instrument_count = conn.execute(text("SELECT COUNT(*) FROM instruments WHERE is_active")).scalar_one()
        alerts_today = conn.execute(
            text("SELECT COUNT(*) FROM alerts WHERE trade_date = :as_of"), {"as_of": as_of}
        ).scalar_one()
        rows = [_Row(*r) for r in conn.execute(_PIVOT_SQL, {"as_of": as_of}).all()]

    out = _RawSnapshot(instrument_count=instrument_count, alerts_today=alerts_today)
    ticker_by_symbol: dict[str, MarketMoverOut] = {}
    movers: list[MarketMoverOut] = []

    for r in rows:
        if r.close_today is None or r.close_prev is None or r.close_prev == 0:
            continue
        # Numeric columns come back as Decimal -- cast to float up front so
        # `pct` can be compared against the plain-float MOVED_PCT_THRESHOLD
        # below (Decimal <-> float comparison raises TypeError otherwise).
        close_today, close_prev = float(r.close_today), float(r.close_prev)
        pct = (close_today - close_prev) / close_prev * 100
        mover = MarketMoverOut(symbol=r.symbol, exchange=r.exchange, close=close_today, change_pct=pct)

        if pct > 0:
            out.up_count += 1
        elif pct < 0:
            out.down_count += 1
        if abs(pct) >= MOVED_PCT_THRESHOLD:
            out.moved_2pct_count += 1

        if r.exchange == "NSE" and r.symbol in TICKER_SYMBOLS:
            ticker_by_symbol[r.symbol] = mover

        if close_today >= MIN_DISPLAY_PRICE:
            movers.append(mover)

            if r.sma200_today is not None and r.sma200_prev is not None and r.close_prev <= r.sma200_prev and r.close_today > r.sma200_today:
                out.golden_cross.append(mover)

            if r.volume_sma20_today and r.volume_today and r.volume_today >= VOLUME_BREAKOUT_RATIO * r.volume_sma20_today:
                out.volume_breakout.append(mover)

    out.golden_cross_count = len(out.golden_cross)
    out.volume_breakout_count = len(out.volume_breakout)
    out.ticker = [ticker_by_symbol[s] for s in TICKER_SYMBOLS if s in ticker_by_symbol]
    out.top_movers = sorted(movers, key=lambda m: abs(m.change_pct), reverse=True)[:TOP_N]
    out.golden_cross = sorted(out.golden_cross, key=lambda m: m.change_pct, reverse=True)[:TOP_N]
    out.volume_breakout = sorted(out.volume_breakout, key=lambda m: m.change_pct, reverse=True)[:TOP_N]
    return out


def build_snapshot(db: Session) -> MarketSnapshotOut:
    as_of = latest_trade_date(db)
    if as_of is None:
        raise ValueError("no price data loaded yet")
    raw = _build_cached(as_of)
    return MarketSnapshotOut(
        as_of=as_of,
        instrument_count=raw.instrument_count,
        up_count=raw.up_count,
        down_count=raw.down_count,
        moved_2pct_count=raw.moved_2pct_count,
        golden_cross_count=raw.golden_cross_count,
        volume_breakout_count=raw.volume_breakout_count,
        alerts_today=raw.alerts_today,
        ticker=raw.ticker,
        top_movers=raw.top_movers,
        golden_cross=raw.golden_cross,
        volume_breakout=raw.volume_breakout,
    )
