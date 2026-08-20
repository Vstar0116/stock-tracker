"""Apply a corporate action's split/bonus factor to historical daily_prices.

CRITICAL: this is the one place that rewrites price history. Get the factor
math backwards and every moving average spanning the action silently corrupts
(CLAUDE.md architecture principle #4). See app/jobs/ingest_corporate_actions.py
for the matching ratio_from/ratio_to storage convention -- it must agree with
the formulas here.

Convention (ratio_from, ratio_to are always positive):
- SPLIT: ratio_from = shares held BEFORE, ratio_to = shares held AFTER.
  factor = ratio_to / ratio_from. A 1-for-5 split (ratio_from=1, ratio_to=5)
  gives factor=5 -- pre-split adjusted prices are divided by 5.
- BONUS: ratio_from = new bonus shares issued, ratio_to = existing shares
  required to qualify. factor = (ratio_from + ratio_to) / ratio_to. A 2:1
  bonus (ratio_from=2, ratio_to=1) gives factor=3 -- pre-issue adjusted
  prices are divided by 3 (1 original + 2 bonus = 3 total shares).

Only `adjusted_close` is ever written. `close` (and open/high/low/volume) are
the as-traded record and must never be touched here.
"""

from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import CorporateAction, DailyPrice

ADJUSTABLE_ACTION_TYPES = ("SPLIT", "BONUS")


def adjustment_factor(action: CorporateAction) -> Decimal:
    """The divisor to apply to adjusted_close for every row before action.ex_date."""
    if action.ratio_from is None or action.ratio_to is None:
        raise ValueError(f"corporate_action id={action.id} is missing ratio_from/ratio_to")

    # Coerce explicitly: ratio_from/ratio_to are plain Python int/float until an
    # object round-trips through the DB (SQLAlchemy only coerces to Decimal at
    # bind time, not on attribute assignment). int/int division in Python
    # silently produces a float, which loses precision for non-clean ratios
    # (e.g. a 5:7 bonus) -- exactly the kind of silent corruption to avoid here.
    ratio_from = Decimal(str(action.ratio_from))
    ratio_to = Decimal(str(action.ratio_to))
    if ratio_from <= 0 or ratio_to <= 0:
        raise ValueError(f"corporate_action id={action.id} has a non-positive ratio")

    if action.action_type == "SPLIT":
        return ratio_to / ratio_from
    if action.action_type == "BONUS":
        return (ratio_from + ratio_to) / ratio_to
    raise ValueError(f"don't know how to adjust action_type={action.action_type!r}")


def apply_corporate_action(db: Session, action: CorporateAction) -> int:
    """Divide adjusted_close by the action's factor for every daily_prices row of
    action.instrument_id dated before action.ex_date, then mark the action applied.

    Idempotent: the UPDATE ... WHERE applied = false below atomically claims the
    action before touching any prices. If another call (or a prior run) already
    applied it, the claim affects 0 rows and this is a no-op -- prices are never
    divided by the same action's factor twice.
    """
    if action.action_type not in ADJUSTABLE_ACTION_TYPES:
        raise ValueError(f"don't know how to adjust action_type={action.action_type!r}")

    claim = db.execute(
        update(CorporateAction)
        .where(CorporateAction.id == action.id, CorporateAction.applied.is_(False))
        .values(applied=True)
    )
    if claim.rowcount == 0:
        db.rollback()
        return 0  # already applied -- nothing to do

    factor = adjustment_factor(action)

    result = db.execute(
        update(DailyPrice)
        .where(DailyPrice.instrument_id == action.instrument_id, DailyPrice.trade_date < action.ex_date)
        .values(adjusted_close=DailyPrice.adjusted_close / factor)
    )
    db.commit()
    db.refresh(action)
    return result.rowcount
