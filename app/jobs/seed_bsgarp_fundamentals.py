"""Seed BS-GARP fundamentals + curated sunrise-sector tags for the researched
stock universe (49 NSE names, screener.in, pulled 2026-09-12 -- see the
BS-GARP implementation memo for the full gate-by-gate writeup).

This is the manual-entry path CLAUDE.md describes for fundamentals -- there is
no ingestion job or API for this data, by design, so a reviewed dataset
embedded in a re-runnable script is the entry mechanism. Re-run whenever the
numbers are refreshed next earnings season: upserts are keyed on
(instrument_id, as_of_date), so a fresh run with the same AS_OF just updates
these same rows.

`fcf_conversion` is never written here -- it's a Postgres GENERATED column
(app/models/fundamentals.py) computed from fcf_per_share/eps_diluted; naming
it in an INSERT/UPDATE is rejected by Postgres outright.

Run with: python -m app.jobs.seed_bsgarp_fundamentals
"""

import argparse
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.models import Fundamental, Instrument
from app.services.sunrise_sectors import SUNRISE_SECTORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_bsgarp_fundamentals")

JOB_NAME = "seed_bsgarp_fundamentals"

# Fixed, not date.today() -- CLAUDE.md idempotency rule: re-running this job
# on a later calendar day must update these same rows, not insert new ones.
# Safely before the daily_prices data this was cross-checked against
# (2026-09-09), so the "latest fundamentals as-of the price date" join in
# app/services/screening.py always sees it.
AS_OF = date(2026, 9, 1)
SOURCE = "manual:screener.in:2026-09-12"

# symbol -> (sector, pe, roce, debt_to_equity, peg, eps_diluted, fcf_per_share)
# peg is None where trailing/3yr growth was negative or a low-base spike made
# the ratio meaningless rather than genuinely attractive (documented per-row
# in the implementation memo) -- entering a misleading PEG would be worse
# than leaving the gate unpopulated (NULL fails every V1/V2/V3-Watch branch
# that reads it, which is the safe default for unreliable data).
# fcf_per_share/eps_diluted are None where the cash-flow statement wasn't
# pulled (HAL, BEL, Solar Industries, Bharat Dynamics) or don't mean what
# they do for an operating company (IRFC -- a lender).
DATA: dict[str, tuple[str, float, float, float | None, float | None, float | None, float | None]] = {
    # -- Defense & Aerospace --
    "HAL":        ("Defense & Aerospace", 35.2, 32.0, 0.0016, 2.20, 136.47, None),
    "BEL":        ("Defense & Aerospace", 48.2, 37.0, None, 1.85, None, None),
    "SOLARINDS":  ("Defense & Aerospace", 155.0, 43.6, None, 3.37, None, None),
    "BDL":        ("Defense & Aerospace", 83.8, 13.8, None, 13.97, None, None),
    "MTARTECH":   ("Defense & Aerospace", 164.0, 15.2, 0.4565, None, 30.65, 20.97),
    "COCHINSHIP": ("Defense & Aerospace", 61.5, 14.5, 0.2673, None, 24.36, -52.69),
    "GRSE":       ("Defense & Aerospace", 34.7, 42.8, 0.0141, 0.81, 65.04, -31.90),
    "MAZDOCK":    ("Defense & Aerospace", 37.4, 41.3, 0.0000, 1.21, 60.30, -72.50),
    # -- Renewable Energy --
    "SUZLON":     ("Renewable Energy", 20.8, 35.5, 0.0294, 0.52, 3.00, 0.62),
    "KPIGREEN":   ("Renewable Energy", 14.4, 12.4, 2.0911, 0.34, 19.85, -134.90),
    "WAAREEENER": ("Renewable Energy", 18.6, 42.8, 0.1534, 0.19, 130.63, -6.53),
    "PREMIERENE": ("Renewable Energy", 298.0, 10.1, 0.3142, 42.57, 3.18, 1.53),
    "JSWENERGY":  ("Renewable Energy", 107.0, 4.79, 0.6168, 8.23, 4.89, 2.32),
    # -- EV & Battery --
    "EXIDEIND":   ("EV & Battery", 29.1, 10.1, 0.0256, 3.64, 13.07, 21.32),
    "OLECTRA":    ("EV & Battery", 57.2, 20.9, 0.2839, 2.49, 20.97, -8.85),
    "SONACOMS":   ("EV & Battery", 63.3, 15.1, 0.0621, 1.71, 10.39, 3.81),
    "UNOMINDA":   ("EV & Battery", 71.6, 19.0, 0.3251, 17.90, 16.90, 6.42),
    "CRAFTSMAN":  ("EV & Battery", 110.0, 10.3, 0.9794, None, 92.08, -249.60),
    "SUPRAJIT":   ("EV & Battery", 24.5, 20.2, 0.2257, 2.72, 19.64, 5.71),
    "ENDURANCE":  ("EV & Battery", 48.7, 21.1, 0.0053, 4.06, 52.06, 5.67),
    # -- Semiconductors & Electronics --
    "DIXON":      ("Semiconductors & Electronics", 66.1, 8.46, 0.0793, None, 126.50, -67.50),
    "KAYNES":     ("Semiconductors & Electronics", 84.6, 9.99, 0.0733, 2.92, 37.91, 25.22),
    # -- Green Hydrogen & Specialty Chemicals --
    "DEEPAKNTR":  ("Green Hydrogen & Specialty Chemicals", 93.9, 7.36, 0.0187, None, 14.00, 19.78),
    "NOCIL":      ("Green Hydrogen & Specialty Chemicals", 40.4, 4.37, 0.0045, None, 3.83, 4.85),
    "PIIND":      ("Green Hydrogen & Specialty Chemicals", 24.3, 18.0, 0.0057, 3.04, 95.67, -17.87),
    "PRAJIND":    ("Green Hydrogen & Specialty Chemicals", 41.9, 13.3, 0.0300, None, 6.49, 11.84),
    "NAVINFLUOR": ("Green Hydrogen & Specialty Chemicals", 76.7, 20.8, 0.0120, 4.79, 97.60, 65.80),
    "VINATIORGA": ("Green Hydrogen & Specialty Chemicals", 26.9, 21.4, 0.0000, 2.07, 48.80, 49.10),
    "CLEAN":      ("Green Hydrogen & Specialty Chemicals", 36.1, 21.7, 0.0006, None, 22.82, 26.27),
    "SRF":        ("Green Hydrogen & Specialty Chemicals", 37.3, 15.9, 0.2674, 0.96, 58.08, 22.69),
    "AARTIIND":   ("Green Hydrogen & Specialty Chemicals", 34.6, 6.84, 0.8314, None, 11.66, -14.17),
    # -- Water Treatment & Infrastructure --
    "WABAG":      ("Water Treatment & Infrastructure", 40.0, 24.0, 0.0440, 1.14, 55.67, 33.17),
    # -- Power Transmission Equipment --
    "TECHNOE":    ("Power Transmission Equipment", 22.0, 15.2, 0.0050, 1.05, 47.13, -51.48),
    "KEC":        ("Power Transmission Equipment", 35.6, 12.5, 0.8410, 3.96, 16.15, -29.62),
    "POLYCAB":    ("Power Transmission Equipment", 44.3, 32.9, 0.0075, 1.53, 172.30, 152.85),
    "APARINDS":   ("Power Transmission Equipment", 61.4, 32.9, 0.1760, 1.57, 243.50, 56.00),
    "CGPOWER":    ("Power Transmission Equipment", 101.0, 28.8, 0.0056, 2.66, 8.36, 3.03),
    "VOLTAMP":    ("Power Transmission Equipment", 35.8, 23.5, 0.0006, 2.56, 305.00, 15.00),
    "TARIL":      ("Power Transmission Equipment", 40.2, 20.4, 0.2944, None, 7.50, -5.23),
    "GENUSPOWER": ("Power Transmission Equipment", 15.4, 25.4, 1.0422, 0.23, 20.17, -16.13),
    "POWERGRID":  ("Power Transmission Equipment", 16.0, 9.79, 1.4820, None, 17.12, 28.40),
    # -- Capital Goods --
    "THERMAX":    ("Capital Goods", 84.8, 15.6, 0.0298, 7.07, 54.08, 23.00),
    "TRITURBINE": ("Capital Goods", 54.7, 39.2, 0.0195, 1.48, 10.53, 1.69),
    "CARBORUNIV": ("Capital Goods", 57.0, 19.2, 0.0000, 5.70, 21.89, 3.68),
    "CUMMINSIND": ("Capital Goods", 59.7, 41.6, 0.0047, 3.98, 84.73, 54.07),
    # -- Digital Infrastructure & Railways --
    "RVNL":       ("Digital Infrastructure & Railways", 51.6, 11.0, 0.5395, None, 3.84, -9.48),
    "IRFC":       ("Digital Infrastructure & Railways", 14.7, 5.64, None, 1.84, 5.36, None),
    "BEML":       ("Digital Infrastructure & Railways", 91.5, 7.92, 0.1062, None, 17.62, -29.76),
    "IRCON":      ("Digital Infrastructure & Railways", 16.9, 11.7, 0.0160, None, 6.57, 0.20),
}

assert set(sector for sector, *_ in DATA.values()) <= set(SUNRISE_SECTORS), "DATA uses a sector not in SUNRISE_SECTORS"


def resolve_instrument_ids(db: Session, symbols: list[str]) -> dict[str, int]:
    rows = db.execute(
        select(Instrument.symbol, Instrument.id).where(Instrument.symbol.in_(symbols), Instrument.exchange == "NSE")
    ).all()
    by_symbol = dict(rows)
    missing = [s for s in symbols if s not in by_symbol]
    if missing:
        raise RuntimeError(f"symbols not found as NSE instruments, aborting: {missing}")
    return by_symbol


def upsert_fundamentals(db: Session, instrument_ids: dict[str, int]) -> int:
    dec = lambda v: None if v is None else Decimal(str(v))  # noqa: E731
    values = [
        {
            "instrument_id": instrument_ids[symbol],
            "as_of_date": AS_OF,
            "pe": dec(pe),
            "roce": dec(roce),
            "debt_to_equity": dec(de),
            "peg": dec(peg),
            "eps_diluted": dec(eps),
            "fcf_per_share": dec(fcfps),
            "source": SOURCE,
        }
        for symbol, (_sector, pe, roce, de, peg, eps, fcfps) in DATA.items()
    ]
    stmt = pg_insert(Fundamental).values(values)
    # fcf_conversion deliberately absent -- generated column, Postgres rejects
    # any INSERT/UPDATE that names it.
    stmt = stmt.on_conflict_do_update(
        index_elements=[Fundamental.instrument_id, Fundamental.as_of_date],
        set_={
            "pe": stmt.excluded.pe,
            "roce": stmt.excluded.roce,
            "debt_to_equity": stmt.excluded.debt_to_equity,
            "peg": stmt.excluded.peg,
            "eps_diluted": stmt.excluded.eps_diluted,
            "fcf_per_share": stmt.excluded.fcf_per_share,
            "source": stmt.excluded.source,
        },
    )
    db.execute(stmt)
    return len(values)


def update_sectors(db: Session, instrument_ids: dict[str, int]) -> int:
    updated = 0
    for symbol, (sector, *_rest) in DATA.items():
        result = db.execute(
            update(Instrument).where(Instrument.id == instrument_ids[symbol]).values(sector=sector)
        )
        if result.rowcount != 1:
            raise RuntimeError(f"expected to update exactly 1 row for {symbol!r}, updated {result.rowcount}")
        updated += 1
    return updated


def run() -> int:
    db = SessionLocal()
    try:
        with track_job_run(db, JOB_NAME, datetime.now(timezone.utc).date()) as tracker:
            instrument_ids = resolve_instrument_ids(db, list(DATA.keys()))
            n_fund = upsert_fundamentals(db, instrument_ids)
            n_sector = update_sectors(db, instrument_ids)
            tracker.rows_processed = n_fund
            logger.info("upserted %d fundamentals rows, tagged %d instruments with a sunrise sector", n_fund, n_sector)
    finally:
        db.close()
    return n_fund


def main(argv=None) -> None:
    argparse.ArgumentParser(description="Seed BS-GARP fundamentals + sunrise-sector tags").parse_args(argv)
    n = run()
    print(f"seeded {n} fundamentals rows (as_of={AS_OF}, source={SOURCE!r})")


if __name__ == "__main__":
    main()
