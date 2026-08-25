"""Ingest the current NSE/BSE equity symbol master into `instruments`.

Run with: python -m app.jobs.ingest_instruments
"""

import csv
import io
from datetime import datetime, timezone

import httpx
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import Instrument

JOB_NAME = "ingest_instruments"

NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_SCRIP_LIST_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_nse_instruments() -> list[dict]:
    # nsearchives.nseindia.com serves static archive files and, unlike the
    # nseindia.com API host, does not require a warmed-up session cookie —
    # a plain browser User-Agent is enough to avoid the bot-block 403.
    resp = httpx.get(NSE_EQUITY_LIST_URL, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=30)
    resp.raise_for_status()

    rows = []
    for raw in csv.DictReader(io.StringIO(resp.text)):
        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        listed_date = None
        if row.get("DATE OF LISTING"):
            try:
                listed_date = datetime.strptime(row["DATE OF LISTING"], "%d-%b-%Y").date()
            except ValueError:
                listed_date = None
        rows.append(
            {
                "symbol": row["SYMBOL"],
                "bse_scrip_code": None,
                "isin": row.get("ISIN NUMBER") or None,
                "company_name": row["NAME OF COMPANY"],
                "series": row.get("SERIES") or None,
                "sector": None,
                "industry": None,
                "listed_date": listed_date,
            }
        )
    return rows


def fetch_bse_instruments() -> list[dict]:
    # api.bseindia.com rejects requests that don't look like a browser call —
    # the reference client (BseIndiaApi) sets User-Agent/Origin/Referer on every
    # request, no session cookie needed beyond that. Group="" (not the default
    # "A") is required to get every active equity group, not just group A.
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.bseindia.com",
        "Referer": "https://www.bseindia.com/",
    }
    params = {"scripcode": "", "Group": "", "industry": "", "segment": "Equity", "status": "Active"}
    resp = httpx.get(BSE_SCRIP_LIST_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return [_bse_row_to_dict(r) for r in resp.json()]


def _bse_row_to_dict(r: dict) -> dict:
    """Map one row of BSE's ListofScripData response to our instrument shape.

    `scrip_id` (e.g. "ABB") is BSE's text short code -- what we store as `symbol`,
    consistent with NSE's text symbols. `SCRIP_CD` (e.g. "500002") is BSE's numeric
    scrip code, which is what TradingView's `BSE:` chart prefix actually resolves --
    `scrip_id` alone 404s there. Captured separately so callers needing either have it.
    """
    return {
        "symbol": r["scrip_id"],
        "bse_scrip_code": r.get("SCRIP_CD") or None,
        "isin": r.get("ISIN_NUMBER") or None,
        "company_name": r.get("Issuer_Name") or r.get("Scrip_Name"),
        "series": r.get("GROUP") or None,
        "sector": None,
        "industry": r.get("INDUSTRY") or None,
        "listed_date": None,
    }


def upsert_instruments(db: Session, exchange: str, rows: list[dict], now: datetime) -> None:
    stmt = pg_insert(Instrument).values(
        [
            {
                "symbol": r["symbol"],
                "exchange": exchange,
                "bse_scrip_code": r["bse_scrip_code"],
                "isin": r["isin"],
                "company_name": r["company_name"],
                "series": r["series"],
                "sector": r["sector"],
                "industry": r["industry"],
                "is_active": True,
                "listed_date": r["listed_date"],
                "first_seen": now,
                "last_seen": now,
            }
            for r in rows
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Instrument.symbol, Instrument.exchange],
        set_={
            "bse_scrip_code": stmt.excluded.bse_scrip_code,
            "isin": stmt.excluded.isin,
            "company_name": stmt.excluded.company_name,
            "series": stmt.excluded.series,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "is_active": True,
            "listed_date": stmt.excluded.listed_date,
            "last_seen": now,
        },
    )
    db.execute(stmt)


def mark_disappeared(db: Session, exchange: str, seen_symbols: set[str]) -> int:
    stmt = (
        update(Instrument)
        .where(
            Instrument.exchange == exchange,
            Instrument.is_active.is_(True),
            Instrument.symbol.notin_(seen_symbols),
        )
        .values(is_active=False)
    )
    result = db.execute(stmt)
    return result.rowcount


def run() -> dict[str, int]:
    """Fetch + upsert both exchanges as one transaction, record the run, return per-exchange counts."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    counts: dict[str, int] = {}
    try:
        with track_job_run(db, JOB_NAME, now.date()) as tracker:
            for exchange, fetch in (("NSE", fetch_nse_instruments), ("BSE", fetch_bse_instruments)):
                rows = fetch()
                if not rows:
                    # An empty fetch almost certainly means a broken source, not a market
                    # with zero listed stocks. Trusting it would mark every instrument on
                    # the exchange inactive via mark_disappeared below.
                    raise RuntimeError(f"{exchange} fetch returned 0 rows — aborting, not deactivating instruments")
                upsert_instruments(db, exchange, rows, now)
                mark_disappeared(db, exchange, {r["symbol"] for r in rows})
                counts[exchange] = len(rows)
            tracker.rows_processed = sum(counts.values())
    finally:
        db.close()
    return counts


def main() -> None:
    counts = run()
    for exchange, count in counts.items():
        print(f"{exchange}: {count} instruments")


if __name__ == "__main__":
    main()
