"""Backtest a screen definition against trading-day history: for each past
day the screen would have matched, compute the forward return of each match
over a few fixed holding periods. Answers "if I'd been running this screen
for the last year, how did its matches actually do?" -- factual historical
stats only, no recommendation (CLAUDE.md: no investment advice).

Reuses services.screening.compile_screen per historical day -- the same
query the nightly run_screens job and the live preview use -- so a backtest
can never disagree with what "matches" means elsewhere in the app.
"""

from __future__ import annotations

from datetime import date
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyPrice
from app.schemas.screen import ScreenRule
from app.services.screening import compile_screen

HORIZONS = (5, 20, 60)  # trading days: ~1 week, ~1 month, ~1 quarter
MIN_LOOKBACK_DAYS = 20
MAX_LOOKBACK_DAYS = 500


class HorizonResult:
    __slots__ = ("horizon_days", "returns")

    def __init__(self, horizon_days: int) -> None:
        self.horizon_days = horizon_days
        self.returns: list[float] = []


class BacktestResult:
    __slots__ = ("as_of", "dates_evaluated", "total_matches", "horizons")

    def __init__(self, as_of: date | None, dates_evaluated: int, total_matches: int, horizons: list[HorizonResult]) -> None:
        self.as_of = as_of
        self.dates_evaluated = dates_evaluated
        self.total_matches = total_matches
        self.horizons = horizons


def run_backtest(db: Session, rule: ScreenRule, lookback_days: int = 250) -> BacktestResult:
    lookback_days = max(MIN_LOOKBACK_DAYS, min(lookback_days, MAX_LOOKBACK_DAYS))
    max_horizon = max(HORIZONS)

    all_dates = db.execute(select(DailyPrice.trade_date).distinct().order_by(DailyPrice.trade_date)).scalars().all()
    if len(all_dates) <= max_horizon + 1:
        return BacktestResult(all_dates[-1] if all_dates else None, 0, 0, [HorizonResult(h) for h in HORIZONS])

    # Only dates with enough later history to score every horizon are
    # testable at all; newest-first, capped at lookback_days.
    testable = all_dates[: len(all_dates) - max_horizon]
    test_dates = testable[-lookback_days:]
    idx = {d: i for i, d in enumerate(all_dates)}

    # ponytail: one bulk price fetch across the whole window instead of a
    # query per (date, horizon) -- bounded by lookback_days * whole-market
    # row count, fine at this app's scale. Upgrade to a windowed SQL join if
    # the market or lookback window ever grows enough to matter.
    window_end = all_dates[idx[test_dates[-1]] + max_horizon]
    price_rows = db.execute(
        select(DailyPrice.instrument_id, DailyPrice.trade_date, DailyPrice.adjusted_close)
        .where(DailyPrice.trade_date.between(test_dates[0], window_end))
    ).all()
    price_map = {(instrument_id, trade_date): float(px) for instrument_id, trade_date, px in price_rows}

    horizons = {h: HorizonResult(h) for h in HORIZONS}
    dates_evaluated = 0
    total_matches = 0

    for as_of_date in test_dates:
        i = idx[as_of_date]
        prev_date = all_dates[i - 1] if i > 0 else None
        stmt = compile_screen(rule, as_of_date, prev_date)
        if stmt is None:
            continue
        rows = db.execute(stmt).mappings().all()
        dates_evaluated += 1
        for row in rows:
            total_matches += 1
            entry_price = price_map.get((row["instrument_id"], as_of_date))
            if not entry_price:
                continue
            for h in HORIZONS:
                fwd_price = price_map.get((row["instrument_id"], all_dates[i + h]))
                if fwd_price is None:
                    continue
                horizons[h].returns.append((fwd_price - entry_price) / entry_price)

    return BacktestResult(all_dates[-1], dates_evaluated, total_matches, list(horizons.values()))


def summarize(result: HorizonResult) -> dict:
    rets = result.returns
    if not rets:
        return dict(
            horizon_days=result.horizon_days, sample_size=0, hit_rate_pct=None,
            avg_return_pct=None, median_return_pct=None, best_return_pct=None, worst_return_pct=None,
        )
    return dict(
        horizon_days=result.horizon_days,
        sample_size=len(rets),
        hit_rate_pct=sum(1 for r in rets if r > 0) / len(rets) * 100,
        avg_return_pct=mean(rets) * 100,
        median_return_pct=median(rets) * 100,
        best_return_pct=max(rets) * 100,
        worst_return_pct=min(rets) * 100,
    )
