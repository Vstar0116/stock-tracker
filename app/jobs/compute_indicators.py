"""Compute technical indicators for every instrument with new price data.

Recomputes each instrument's full indicator history from its full price
history (needed for correct window seeding -- e.g. SMA-200 at any date
depends on the preceding 199 days), but only upserts rows newer than what's
already stored: indicator values are backward-looking only, so a value
already written for an old date can never change when new price data arrives.

# ponytail: full-history recompute per instrument per run, O(n) per instrument
# (dominated by the Wilder-smoothing Python loops in RSI/ATR). Fine at this
# app's scale (~7,500 instruments, a few thousand bars each); if that stops
# being true, upgrade path is incremental computation seeded from the last
# stored indicator row instead of recomputing from day one every time.

Run with: python -m app.jobs.compute_indicators
"""

import argparse
import logging
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import DailyPrice, Indicator
from app.services.indicators import compute_all_indicators

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("compute_indicators")

JOB_NAME = "compute_indicators"

INDICATOR_COLUMNS = [
    "sma_20",
    "sma_50",
    "sma_100",
    "sma_200",
    "ema_20",
    "ema_50",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "atr_14",
    "volume_sma_20",
    "high_52w",
    "low_52w",
]


def instruments_needing_recompute(db: Session, force: bool = False) -> dict[int, date | None]:
    """instrument_id -> latest indicators.trade_date already stored (None if
    none yet at all, or if force=True), for every instrument that should be
    (re)computed.

    force=True treats every instrument as if it had no indicators yet -- use
    this after a backfill inserts price history *older* than dates indicators
    were already computed for. The normal incremental check below only looks
    at whether NEWER price data has arrived, so it would never revisit (and
    fix) an already-written sma_200/high_52w/etc. that was originally computed
    from too little trailing history.
    """
    latest_price = dict(
        db.execute(
            select(DailyPrice.instrument_id, func.max(DailyPrice.trade_date)).group_by(DailyPrice.instrument_id)
        ).all()
    )
    if force:
        return {instrument_id: None for instrument_id in latest_price}

    latest_indicator = dict(
        db.execute(
            select(Indicator.instrument_id, func.max(Indicator.trade_date)).group_by(Indicator.instrument_id)
        ).all()
    )
    return {
        instrument_id: latest_indicator.get(instrument_id)
        for instrument_id, price_date in latest_price.items()
        if latest_indicator.get(instrument_id) is None or price_date > latest_indicator[instrument_id]
    }


def load_price_history(db: Session, instrument_id: int) -> pd.DataFrame:
    rows = db.execute(
        select(
            DailyPrice.trade_date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
            DailyPrice.adjusted_close,
            DailyPrice.volume,
        )
        .where(DailyPrice.instrument_id == instrument_id)
        .order_by(DailyPrice.trade_date)
    ).all()
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "adjusted_close", "volume"])
    return df.set_index("trade_date")


def upsert_indicators(db: Session, instrument_id: int, indicators_df: pd.DataFrame) -> int:
    values = []
    for trade_date, row in indicators_df.iterrows():
        record = {"instrument_id": instrument_id, "trade_date": trade_date}
        for col in INDICATOR_COLUMNS:
            v = row[col]
            record[col] = None if pd.isna(v) else float(v)
        values.append(record)

    if not values:
        return 0

    stmt = pg_insert(Indicator).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Indicator.instrument_id, Indicator.trade_date],
        set_={col: getattr(stmt.excluded, col) for col in INDICATOR_COLUMNS},
    )
    result = db.execute(stmt)
    return result.rowcount


def run(force: bool = False) -> int:
    db = SessionLocal()
    total_rows = 0
    try:
        with track_job_run(db, JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            targets = instruments_needing_recompute(db, force=force)
            logger.info(
                "%d instruments %s", len(targets), "forced to full recompute" if force else "have new price data"
            )

            for instrument_id, latest_indicator_date in targets.items():
                prices = load_price_history(db, instrument_id)
                if prices.empty:
                    continue
                indicators_df = compute_all_indicators(prices)
                if latest_indicator_date is not None:
                    indicators_df = indicators_df[indicators_df.index > latest_indicator_date]
                total_rows += upsert_indicators(db, instrument_id, indicators_df)

            tracker.rows_processed = total_rows
            logger.info("computed indicators for %d instruments, %d rows upserted", len(targets), total_rows)
    finally:
        db.close()
    return total_rows


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Compute technical indicators from daily_prices")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute every instrument's full history, even where indicators already look up to date "
        "(use after a backfill adds price data older than what indicators were already computed from)",
    )
    args = parser.parse_args(argv)

    total_rows = run(force=args.force)
    print(f"computed indicators: {total_rows} rows upserted")


if __name__ == "__main__":
    main()
