"""Populate `instruments.industry` market-wide from NSE's free Nifty Total
Market constituent list -- the only sector/industry data source in this app
that isn't a single BS-GARP-curated dataset (see app/services/sunrise_sectors
and app/jobs/seed_bsgarp_fundamentals.py for that, which sets `.sector`
instead). NSE's raw industry taxonomy (23 broad buckets -- "Capital Goods",
"Financial Services", "Power", etc.) is intentionally kept in the separate
`industry` column rather than `sector`, so it never collides with or dilutes
the narrower, hand-picked "sunrise sector" grouping BS-GARP screens filter on.

Covers whatever NSE's Nifty Total Market index currently contains (~750
names as of 2026-09) -- a broad slice of the market, not literally all
~7,600 instruments this app tracks (there is no free, complete, per-symbol
industry feed for the rest). Unmatched symbols in the file (already-delisted
tickers not yet in the file's next refresh) and instruments this app tracks
that fall outside the file entirely are both expected and non-fatal.

Run with: python -m app.jobs.ingest_sector_classification
"""

import argparse
import csv
import io
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import Instrument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_sector_classification")

JOB_NAME = "ingest_sector_classification"

NIFTY_TOTAL_MARKET_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"

# Same host/header pattern as app/jobs/ingest_instruments.py's
# fetch_nse_instruments() -- nsearchives.nseindia.com serves static archive
# files and doesn't require a warmed-up session cookie, just a browser UA.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_industry_rows() -> list[dict[str, str]]:
    """Columns confirmed live: 'Company Name,Industry,Symbol,Series,ISIN Code'."""
    resp = httpx.get(NIFTY_TOTAL_MARKET_URL, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    rows = []
    for raw in csv.DictReader(io.StringIO(resp.text)):
        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        if row.get("Symbol") and row.get("Industry"):
            rows.append({"symbol": row["Symbol"], "industry": row["Industry"]})
    return rows


def update_industries(db: Session, rows: list[dict[str, str]]) -> tuple[int, int]:
    """Only updates instruments already tracked as NSE -- never inserts.
    Returns (matched, unmatched_in_file)."""
    tracked = set(db.execute(select(Instrument.symbol).where(Instrument.exchange == "NSE")).scalars().all())
    to_update = [r for r in rows if r["symbol"] in tracked]
    unmatched = len(rows) - len(to_update)

    if to_update:
        # Plain Core Table (not the mapped class) -- executemany-style bulk
        # UPDATE by an arbitrary WHERE needs a non-ORM-enabled statement,
        # otherwise SQLAlchemy 2.0 tries its "ORM bulk UPDATE by primary key"
        # path and demands a PK in every params dict.
        table = Instrument.__table__
        stmt = (
            update(table)
            .where(table.c.symbol == bindparam("b_symbol"), table.c.exchange == "NSE")
            .values(industry=bindparam("b_industry"))
        )
        db.execute(stmt, [{"b_symbol": r["symbol"], "b_industry": r["industry"]} for r in to_update])
    return len(to_update), unmatched


def run() -> int:
    db = SessionLocal()
    try:
        with track_job_run(db, JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            rows = fetch_industry_rows()
            if not rows:
                raise RuntimeError("Nifty Total Market list fetch returned 0 rows -- aborting")
            matched, unmatched = update_industries(db, rows)
            tracker.rows_processed = matched
            logger.info(
                "industry classified for %d instruments (%d file rows had no matching tracked NSE instrument)",
                matched,
                unmatched,
            )
    finally:
        db.close()
    return matched


def main(argv=None) -> None:
    argparse.ArgumentParser(description="Populate instruments.industry from NSE's free Nifty Total Market list").parse_args(argv)
    n = run()
    print(f"classified industry for {n} instruments")


if __name__ == "__main__":
    main()
