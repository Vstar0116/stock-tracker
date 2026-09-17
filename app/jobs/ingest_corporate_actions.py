"""Ingest announced stock splits, bonuses, and dividends from NSE/BSE into `corporate_actions`.

Sources verified 2026-08-18:
- NSE: https://www.nseindia.com/api/corporates-corporateActions?index=equities
  &from_date=DD-MM-YYYY&to_date=DD-MM-YYYY -- confirmed working with just a
  browser User-Agent, no session cookie needed (unlike some other nseindia.com
  API endpoints). Free-text `subject` field carries the ratio, e.g.
  "Bonus 2:1" or "Face Value Split (Sub-Division) - From Rs 10/- Per Share To
  Re 1/- Per Share".
- BSE: https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?ddlcategorys=E&
  ddlindustrys=&segment=0&strSearch=D&Fdate=YYYYMMDD&TDate=YYYYMMDD -- found by
  reading the reference client (github.com/BennyThadikaran/BseIndiaApi)
  `actions()` method, confirmed live. Needs User-Agent/Origin/Referer headers.
  Free-text `Purpose` field, e.g. "Bonus issue 2:1" or "Stock  Split From
  Rs.10/- to Rs.1/-".

Splits, bonuses, and dividends are ingested; other action types (rights
issues, buybacks, etc.) are not recognized and are silently skipped. Only
SPLIT/BONUS carry a ratio price_adjustment.py can turn into a price
adjustment factor -- DIVIDEND rows carry an amount in `value` instead and are
informational only (dividend yield), never adjusted into price history.
daily_pipeline.py's action_type filter (ADJUSTABLE_ACTION_TYPES) is what
keeps DIVIDEND rows away from apply_corporate_action().

CRITICAL ratio convention (see services/price_adjustment.py for the matching
factor math) -- get this backwards and prices silently corrupt:
- SPLIT: ratio_from = shares held BEFORE, ratio_to = shares held AFTER.
  A 10-for-1 split (face value Rs 10 -> Re 1) is ratio_from=1, ratio_to=10.
  Announcements are worded as face values, which move the OPPOSITE direction
  from share count (smaller face value = more shares), so the parser swaps
  the announced "from/to" order when it stores them: the announced *new*
  face value becomes ratio_from, the announced *old* face value becomes
  ratio_to.
- BONUS: ratio_from = new bonus shares issued, ratio_to = existing shares
  required to qualify. "Bonus 2:1" (2 new per 1 held) is stored exactly as
  announced: ratio_from=2, ratio_to=1. No swap -- announcement order already
  matches share count order for bonuses.

Run with: python -m app.jobs.ingest_corporate_actions [--from YYYY-MM-DD --to YYYY-MM-DD]
"""

import argparse
import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import CorporateAction, Instrument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_corporate_actions")

JOB_NAME = "ingest_corporate_actions"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

NSE_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions"
BSE_ACTIONS_URL = "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"

# "From Rs 10/- Per Share To Re 1/- Per Share" / "From Rs.10/- to Rs.1/-" -- both
# exchanges phrase splits as an old/new face value, "Rs" or "Re" (singular, for 1).
SPLIT_RE = re.compile(r"R[se]\.?\s*(\d+(?:\.\d+)?)\s*/-.*?\bto\b\s*R[se]\.?\s*(\d+(?:\.\d+)?)\s*/-", re.IGNORECASE)
# "Bonus 2:1" / "Bonus issue 2:1"
BONUS_RE = re.compile(r"bonus\D*?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
# "Interim Dividend - Rs 5 Per Share" (NSE) / "Dividend - Rs. - 5.0000" (BSE) --
# both phrase it as "dividend" ... "Rs"/"Re" ... amount, with varying dashes/
# dots in between. Amount only, no ratio -- doesn't touch adjusted_close.
DIVIDEND_RE = re.compile(r"dividend.*?R[se]\.?\s*-?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _parse_action_text(text: str) -> tuple[str, str, str | None] | None:
    """Classify + extract ratio/amount from one announcement's free-text
    description. Returns (action_type, ratio_from, ratio_to) as strings for
    SPLIT/BONUS, or (action_type, amount, None) for DIVIDEND, or None if this
    announcement isn't a split/bonus/dividend we recognize.
    """
    if match := SPLIT_RE.search(text):
        old_face, new_face = match.groups()
        # Swapped deliberately -- see module docstring. Share count moves the
        # opposite way from face value, so (new, old) here gives (before, after).
        return "SPLIT", new_face, old_face
    if match := BONUS_RE.search(text):
        new_shares, existing_shares = match.groups()
        return "BONUS", new_shares, existing_shares
    if match := DIVIDEND_RE.search(text):
        return "DIVIDEND", match.group(1), None
    return None


def _action_row(symbol: str, ex_date: date, parsed: tuple[str, str, str | None], raw_description: str | None) -> dict:
    """Shared by both fetchers: (action_type, ratio_from, ratio_to) for
    SPLIT/BONUS, (action_type, amount, None) for DIVIDEND -- see
    _parse_action_text. ratio_from/ratio_to stay unset (not None) for a
    DIVIDEND row and vice versa, matching load_actions' r.get(...) reads."""
    action_type, first, second = parsed
    row = {"symbol": symbol, "ex_date": ex_date, "action_type": action_type, "raw_description": raw_description}
    if action_type == "DIVIDEND":
        row["value"] = first
    else:
        row["ratio_from"] = first
        row["ratio_to"] = second
    return row


def fetch_nse_actions(from_date: date, to_date: date) -> list[dict]:
    params = {
        "index": "equities",
        "from_date": from_date.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    resp = httpx.get(
        NSE_ACTIONS_URL,
        params=params,
        headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()

    rows = []
    for r in resp.json():
        parsed = _parse_action_text(r.get("subject", ""))
        if parsed is None:
            continue
        rows.append(
            _action_row(
                symbol=r["symbol"],
                ex_date=datetime.strptime(r["exDate"], "%d-%b-%Y").date(),
                parsed=parsed,
                raw_description=r.get("subject"),
            )
        )
    return rows


def fetch_bse_actions(from_date: date, to_date: date) -> list[dict]:
    params = {
        "ddlcategorys": "E",  # by ex-date
        "ddlindustrys": "",
        "segment": "0",  # equity
        "strSearch": "D",
        "Fdate": from_date.strftime("%Y%m%d"),
        "TDate": to_date.strftime("%Y%m%d"),
    }
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.bseindia.com",
        "Referer": "https://www.bseindia.com/",
    }
    resp = httpx.get(BSE_ACTIONS_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()

    rows = []
    for r in resp.json():
        parsed = _parse_action_text(r.get("Purpose", ""))
        if parsed is None:
            continue
        rows.append(
            _action_row(
                symbol=r["short_name"],
                ex_date=datetime.strptime(r["exdate"], "%Y%m%d").date(),
                parsed=parsed,
                raw_description=r.get("Purpose"),
            )
        )
    return rows


def load_actions(db: Session, exchange: str, rows: list[dict]) -> tuple[int, list[str]]:
    """Match rows to instruments and insert into corporate_actions (applied=false).
    Existing rows for the same (instrument, ex_date, action_type) are left untouched --
    never silently overwrite a ratio that may already have been applied to prices.
    Returns (rows_inserted, unmatched_symbols).
    """
    symbols = {r["symbol"] for r in rows}
    instrument_ids = dict(
        db.execute(
            select(Instrument.symbol, Instrument.id).where(
                Instrument.exchange == exchange, Instrument.symbol.in_(symbols)
            )
        ).all()
    )

    values = []
    unmatched = []
    for r in rows:
        instrument_id = instrument_ids.get(r["symbol"])
        if instrument_id is None:
            unmatched.append(r["symbol"])
            continue
        values.append(
            {
                "instrument_id": instrument_id,
                "ex_date": r["ex_date"],
                "action_type": r["action_type"],
                # DIVIDEND rows carry "value" (an amount, not a ratio) instead
                # of ratio_from/ratio_to -- see _action_row(). applied stays
                # False for DIVIDEND too, but daily_pipeline's action_type
                # filter (see ADJUSTABLE_ACTION_TYPES) means nothing ever
                # tries to adjustment_factor() it.
                "ratio_from": r.get("ratio_from"),
                "ratio_to": r.get("ratio_to"),
                "value": r.get("value"),
                "applied": False,
                "raw_description": r["raw_description"],
            }
        )

    if not values:
        return 0, unmatched

    stmt = pg_insert(CorporateAction).values(values)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[CorporateAction.instrument_id, CorporateAction.ex_date, CorporateAction.action_type]
    )
    result = db.execute(stmt)
    return result.rowcount, unmatched


def run(from_date: date, to_date: date) -> dict[str, int]:
    db = SessionLocal()
    counts: dict[str, int] = {}
    try:
        with track_job_run(db, JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            for exchange, fetch_fn in (("NSE", fetch_nse_actions), ("BSE", fetch_bse_actions)):
                rows = fetch_fn(from_date, to_date)
                inserted, unmatched = load_actions(db, exchange, rows)
                if unmatched:
                    distinct = sorted(set(unmatched))
                    logger.warning(
                        "%s: %d actions had no matching instrument, e.g. %s",
                        exchange,
                        len(unmatched),
                        ", ".join(distinct[:20]),
                    )
                counts[exchange] = inserted
                logger.info("%s: found %d split/bonus announcements, inserted %d new", exchange, len(rows), inserted)
            tracker.rows_processed = sum(counts.values())
    finally:
        db.close()
    return counts


def parse_args(argv=None) -> tuple[date, date]:
    parser = argparse.ArgumentParser(description="Ingest NSE/BSE split & bonus announcements")
    parser.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD (default: 90 days from --from)")
    args = parser.parse_args(argv)

    def _parse_date(raw: str, flag: str) -> date:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"{flag} must be YYYY-MM-DD")

    start = _parse_date(args.from_date, "--from") if args.from_date else date.today()
    end = _parse_date(args.to_date, "--to") if args.to_date else start + timedelta(days=90)
    if start > end:
        parser.error("--from must not be after --to")
    return start, end


def main(argv=None) -> None:
    from_date, to_date = parse_args(argv)
    counts = run(from_date, to_date)
    for exchange, count in counts.items():
        print(f"{exchange}: {count} new corporate actions")


if __name__ == "__main__":
    main()
