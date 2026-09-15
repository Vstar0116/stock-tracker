"""Create the 28 BS-GARP saved screens (7 grading variants x 4 RSI zones) for
one user. Screens are strictly per-user (Screen.user_id is a required FK,
there is no shared/global concept in this app), so a target user must already
exist -- create one with `python -m app.jobs.create_user` first (interactive
password prompt; deliberately not something this script does).

Bypasses the HTTP API and inserts via SessionLocal() directly, same pattern
as app/jobs/create_user.py -- Screen has no invariant beyond its user_id FK
that the API layer would otherwise enforce.

Get-or-create by (user_id, name) in Python, since Screen has no unique
constraint to lean on for an ON CONFLICT. Safe for the single-operator,
manually-run use this script is for; a race between two concurrent runs could
double-insert, same caveat as any other "check then insert" script.

Run with: python -m app.jobs.seed_bsgarp_screens --email a@b.com
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Screen, User
from app.schemas.screen import parse_screen_definition
from app.services.sunrise_sectors import SUNRISE_SECTORS

# --- grade bodies ------------------------------------------------------
# Each is a Group(and, [...]) of Compare/Between leaves on the fields already
# registered in app/schemas/screen.py's FUNDAMENTAL_FIELDS. `_sector` is
# appended below per-variant (sunrise-restricted vs all-sectors).

def _peg(op: str, value: float) -> dict:
    return {"type": "compare", "op": op, "field": "peg", "value": value}


def _peg_between(low: float, high: float) -> dict:
    return {"type": "between", "field": "peg", "low": low, "high": high}


def _fcf(op: str, value: float) -> dict:
    return {"type": "compare", "op": op, "field": "fcf_conversion", "value": value}


def _fcf_between(low: float, high: float) -> dict:
    return {"type": "between", "field": "fcf_conversion", "low": low, "high": high}


def _roce(op: str, value: float) -> dict:
    return {"type": "compare", "op": op, "field": "roce", "value": value}


def _de(op: str, value: float) -> dict:
    return {"type": "compare", "op": op, "field": "debt_to_equity", "value": value}


def _and(*rules: dict) -> dict:
    return {"type": "group", "op": "and", "rules": list(rules)}


def _or(*rules: dict) -> dict:
    return {"type": "group", "op": "or", "rules": list(rules)}


GRADE_BODIES: dict[str, dict] = {
    "V1 — Deep Value": _and(_peg("lte", 1.0), _fcf("gte", 75), _roce("gte", 18), _de("lte", 0.2)),
    "V2 — Fair Value Compounder": _and(_peg_between(1.01, 1.50), _fcf_between(50, 74), _roce("gte", 15), _de("lte", 0.4)),
    "V2 — Relaxed FCF Floor 20%": _and(_peg_between(1.01, 1.50), _fcf_between(20, 74), _roce("gte", 15), _de("lte", 0.4)),
    "V2 — Relaxed PEG Ceiling 2.0": _and(_peg_between(1.01, 2.00), _fcf_between(50, 74), _roce("gte", 15), _de("lte", 0.4)),
    # "fails exactly one of the four V2 gates" as an OR of 4 mutually-exclusive
    # branches, each holding the other three gates strict. Verified sound
    # (see the implementation memo): no branch can match a stock failing more
    # than one gate, and every real one-gate-miss researched (Garden Reach,
    # Mazagon Dock, Suzlon, KEI, Triveni Turbine, Polycab, Apar, HAL, BEL)
    # falls into exactly the branch its actual failing metric predicts.
    "V3-Watch — one gate short": _or(
        _and(_fcf("gte", 50), _roce("gte", 15), _de("lte", 0.4), _peg("gt", 1.50)),   # PEG-miss
        _and(_peg("lte", 1.50), _roce("gte", 15), _de("lte", 0.4), _fcf("lt", 50)),   # FCF-miss
        _and(_peg("lte", 1.50), _fcf("gte", 50), _de("lte", 0.4), _roce("lt", 15)),   # ROCE-miss
        _and(_peg("lte", 1.50), _fcf("gte", 50), _roce("gte", 15), _de("gt", 0.4)),   # D/E-miss
    ),
}

# Which grades additionally ship an "all sectors" (no sunrise restriction)
# counterpart, per the report's Option 4.
ALL_SECTORS_VARIANTS = ("V1 — Deep Value", "V2 — Fair Value Compounder")

ZONE_BODIES: dict[str, dict] = {
    "Zone A": {"type": "compare", "op": "lte", "field": "rsi_14", "value": 55},
    "Zone B": {"type": "between", "field": "rsi_14", "low": 56, "high": 65},
    "Zone C": {"type": "between", "field": "rsi_14", "low": 66, "high": 71},
    "Zone D": {"type": "compare", "op": "gte", "field": "rsi_14", "value": 72},
}

SECTOR_RULE = {"type": "in", "field": "sector", "values": list(SUNRISE_SECTORS)}


def build_definitions() -> dict[str, dict]:
    """name -> validated rule-tree dict, for every (variant, zone) cell."""
    definitions: dict[str, dict] = {}

    def add(label: str, sector_scope: str, grade_body: dict) -> None:
        for zone_label, zone_body in ZONE_BODIES.items():
            rules = [grade_body, zone_body]
            if sector_scope == "sunrise sectors":
                rules.insert(0, SECTOR_RULE)
            name = f"BS-GARP {label} ({sector_scope}) — {zone_label}"
            definition = _and(*rules)
            parse_screen_definition(definition)  # fail fast on any typo before touching the DB
            definitions[name] = definition

    for label, body in GRADE_BODIES.items():
        add(label, "sunrise sectors", body)
    for label in ALL_SECTORS_VARIANTS:
        add(label, "all sectors", GRADE_BODIES[label])

    return definitions


def get_or_create_screens(db: Session, user_id: int, definitions: dict[str, dict]) -> tuple[int, int]:
    existing_names = set(
        db.execute(select(Screen.name).where(Screen.user_id == user_id)).scalars().all()
    )
    created = 0
    for name, definition in definitions.items():
        if name in existing_names:
            continue
        db.add(Screen(user_id=user_id, name=name, description="BS-GARP protocol preset", definition=definition, is_active=True))
        created += 1
    db.commit()
    return created, len(definitions) - created


def run(email: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            raise ValueError(f"no user with email {email!r} -- create one first with app.jobs.create_user")
        definitions = build_definitions()
        created, skipped = get_or_create_screens(db, user.id, definitions)
        return created, skipped
    finally:
        db.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Create the 28 BS-GARP saved screens for one user")
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    created, skipped = run(args.email)
    print(f"created {created} screens, {skipped} already existed")


if __name__ == "__main__":
    main()
