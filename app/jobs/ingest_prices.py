"""Daily OHLCV ingestion from NSE/BSE bhavcopy (UDiFF format) into `daily_prices`.

Run with: python -m app.jobs.ingest_prices [--date YYYY-MM-DD]
"""

import argparse
import csv
import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import DailyPrice, Instrument, JobRun

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_prices")

IST_OFFSET = timedelta(hours=5, minutes=30)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 3 retries, 5s/10s/20s apart -- enough to ride out the file being published a
# few minutes late without hammering the exchange while we wait. Only applies
# to today's date: a miss on a PAST date is permanent (holiday, or before the
# exchange's archive starts) and retrying it can never succeed, so those fail
# on the first attempt -- otherwise a multi-year backfill spends most of its
# time asleep in backoff for dates that will never exist.
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 5


class BhavcopyNotPublished(Exception):
    """The exchange responded but the file for this date doesn't exist yet."""


def _fetch_with_backoff(fetch_once, label: str, trade_date: date) -> bytes | None:
    """Call fetch_once() with retries. Returns None (not an exception) when the
    file genuinely isn't available after all attempts -- the caller treats that
    as "holiday or not yet published", not a crash.
    """
    max_attempts = MAX_ATTEMPTS if trade_date >= date.today() else 1
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_once()
        except (httpx.HTTPError, BhavcopyNotPublished) as exc:
            last_exc = exc
            logger.info("%s: not available on attempt %d/%d (%s)", label, attempt, max_attempts, exc)
        if attempt < max_attempts:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    logger.info("%s: giving up after %d attempts (last: %s)", label, max_attempts, last_exc)
    return None


def fetch_nse_bhavcopy(trade_date: date) -> bytes | None:
    # Confirmed 2026-08-18 against nsearchives.nseindia.com: the old
    # cmDDMMMYYYYbhav.csv.zip format is gone. Current format is the UDiFF
    # "common bhavcopy" zip, one CSV covering all CM-segment instruments.
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"

    def once() -> bytes:
        resp = httpx.get(url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=30)
        resp.raise_for_status()  # NSE gives a real 404 when the file isn't published yet
        return resp.content

    zip_bytes = _fetch_with_backoff(once, f"NSE bhavcopy {trade_date.isoformat()}", trade_date)
    if zip_bytes is None:
        return None
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return zf.read(zf.namelist()[0])


def fetch_bse_bhavcopy(trade_date: date) -> bytes | None:
    # Confirmed 2026-08-18: same UDiFF CSV format as NSE, served unzipped, at
    # this www.bseindia.com download path (there is no bseindia.com archives
    # host for it). Requires a Referer header or the request is rejected.
    url = f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.CSV"

    def once() -> bytes:
        resp = httpx.get(
            url,
            headers={"User-Agent": BROWSER_USER_AGENT, "Referer": "https://www.bseindia.com/"},
            timeout=30,
        )
        resp.raise_for_status()
        # Unlike NSE, BSE does NOT 404 when the file is missing -- it returns
        # 200 with the normal bseindia.com homepage HTML instead. Detect that
        # "soft 404" by checking the body is actually the CSV we expect.
        if not resp.content.startswith(b"TradDt,"):
            raise BhavcopyNotPublished("got HTML instead of CSV (file not published yet)")
        return resp.content

    return _fetch_with_backoff(once, f"BSE bhavcopy {trade_date.isoformat()}", trade_date)


def parse_bhavcopy(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append(
                {
                    "symbol": row["TckrSymb"],
                    "series": row.get("SctySrs") or None,
                    "open": Decimal(row["OpnPric"]),
                    "high": Decimal(row["HghPric"]),
                    "low": Decimal(row["LwPric"]),
                    "close": Decimal(row["ClsPric"]),
                    "volume": int(Decimal(row["TtlTradgVol"])),
                }
            )
        except (InvalidOperation, KeyError, ValueError):
            continue  # blank/malformed row (e.g. a halted security) -- skip, don't crash the run
    return rows


def load_prices(db: Session, exchange: str, trade_date: date, rows: list[dict]) -> tuple[int, list[str]]:
    """Match parsed rows to instruments and upsert daily_prices. Returns (rows_loaded, unmatched_symbols).

    A symbol can appear more than once in one day's bhavcopy under a different
    SctySrs (e.g. a block-deal reporting row alongside the normal EQ row for
    the same stock) -- these share an instrument_id, so blindly loading both
    trips Postgres's "ON CONFLICT DO UPDATE affects row a second time" error.
    We pick the row whose series matches the instrument's recorded series.
    """
    symbols = {r["symbol"] for r in rows}
    instruments = {
        symbol: (instrument_id, series)
        for symbol, instrument_id, series in db.execute(
            select(Instrument.symbol, Instrument.id, Instrument.series).where(
                Instrument.exchange == exchange, Instrument.symbol.in_(symbols)
            )
        ).all()
    }

    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    values = []
    unmatched = []
    ambiguous = 0
    for symbol, candidates in by_symbol.items():
        instrument = instruments.get(symbol)
        if instrument is None:
            unmatched.append(symbol)
            continue
        instrument_id, series = instrument
        chosen = candidates[0]
        if len(candidates) > 1:
            ambiguous += 1
            chosen = next((c for c in candidates if c["series"] == series), candidates[0])
        values.append(
            {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "open": chosen["open"],
                "high": chosen["high"],
                "low": chosen["low"],
                "close": chosen["close"],
                # ponytail: adjusted_close = close (unadjusted) until the corporate-action
                # adjustment job exists; that job owns rewriting this column, not this one.
                "adjusted_close": chosen["close"],
                "volume": chosen["volume"],
            }
        )

    if ambiguous:
        logger.warning(
            "%s: %d symbols had multiple bhavcopy rows for %s (different series) -- kept the one matching the instrument's series",
            exchange,
            ambiguous,
            trade_date,
        )

    if not values:
        return 0, unmatched

    stmt = pg_insert(DailyPrice).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyPrice.instrument_id, DailyPrice.trade_date],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "volume": stmt.excluded.volume,
        },
    )
    db.execute(stmt)
    return len(values), unmatched


def _job_run(job_name: str, run_date: date, status: str, started_at: datetime, rows_processed: int, error_message: str | None = None) -> JobRun:
    return JobRun(
        job_name=job_name,
        run_date=run_date,
        status=status,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        rows_processed=rows_processed,
        error_message=error_message,
    )


def run_for_exchange(db: Session, exchange: str, trade_date: date, fetch_fn) -> tuple[str, int]:
    """Fetch + load one exchange's bhavcopy for one date. Returns (status, rows_processed)
    so callers (the CLI here, and jobs/backfill_prices.py) can report on what happened
    without re-querying job_runs."""
    job_name = f"ingest_prices_{exchange.lower()}"
    started_at = datetime.now(timezone.utc)

    if trade_date.weekday() >= 5:  # Saturday/Sunday
        logger.info("%s: %s is a weekend -- market closed, skipping", exchange, trade_date)
        db.add(_job_run(job_name, trade_date, "skipped", started_at, 0, "weekend, market closed"))
        db.commit()
        return "skipped", 0

    try:
        raw = fetch_fn(trade_date)
        if raw is None:
            msg = "bhavcopy not available after retries (market holiday or not yet published)"
            logger.info("%s: %s -- skipping %s", exchange, msg, trade_date)
            db.add(_job_run(job_name, trade_date, "skipped", started_at, 0, msg))
            db.commit()
            return "skipped", 0

        rows = parse_bhavcopy(raw)
        loaded, unmatched = load_prices(db, exchange, trade_date, rows)
        if unmatched:
            distinct = sorted(set(unmatched))
            logger.warning(
                "%s: %d bhavcopy rows had no matching instrument, e.g. %s%s",
                exchange,
                len(unmatched),
                ", ".join(distinct[:20]),
                " ..." if len(distinct) > 20 else "",
            )

        db.add(_job_run(job_name, trade_date, "success", started_at, loaded))
        db.commit()
        logger.info("%s: loaded %d rows for %s", exchange, loaded, trade_date)
        return "success", loaded
    except Exception as exc:
        db.rollback()
        logger.exception("%s: failed to load bhavcopy for %s", exchange, trade_date)
        db.add(_job_run(job_name, trade_date, "failed", started_at, 0, str(exc)[:2000]))
        db.commit()
        return "failed", 0


def most_recent_trading_day(today_ist: date) -> date:
    """Roll back to the most recent Mon-Fri. Doesn't know about market holidays --
    those are caught by the not-published retry/skip path in run_for_exchange."""
    d = today_ist
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def parse_args(argv=None) -> date:
    parser = argparse.ArgumentParser(description="Ingest NSE/BSE daily bhavcopy into daily_prices")
    parser.add_argument("--date", help="Trade date as YYYY-MM-DD (default: most recent trading day, IST)")
    args = parser.parse_args(argv)
    if args.date:
        try:
            return datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--date must be YYYY-MM-DD")
    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()
    return most_recent_trading_day(today_ist)


def run(trade_date: date) -> None:
    db = SessionLocal()
    try:
        nse_status, _ = run_for_exchange(db, "NSE", trade_date, fetch_nse_bhavcopy)
        bse_status, _ = run_for_exchange(db, "BSE", trade_date, fetch_bse_bhavcopy)
    finally:
        db.close()
    # run_for_exchange records its own job_runs row and swallows exceptions rather
    # than raising, so callers checking for success (e.g. the daily pipeline) need
    # this instead of a try/except around run() -- otherwise a failed fetch looks
    # identical to a successful one from here.
    if nse_status == "failed" or bse_status == "failed":
        raise RuntimeError(f"ingest_prices failed for {trade_date}: NSE={nse_status}, BSE={bse_status}")


def main(argv=None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
