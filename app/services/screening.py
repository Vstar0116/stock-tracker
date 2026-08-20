"""Compile a screen's rule tree (app/schemas/screen.py) into a parameterised
SQLAlchemy query against instruments + daily_prices + indicators +
fundamentals.

All values from the rule tree (constants, `in` lists, `between` bounds) are
passed to SQLAlchemy as bound parameters via normal Python operator overloads
(`col > value`, `col.in_(values)`, ...) -- nothing is ever string-formatted
into SQL. Field *names* never reach SQL as strings either: they're looked up
in FIELD_COLUMNS to get an actual ORM column object, so a field name that
isn't a key in that dict can't produce a query at all (and schemas/screen.py
already rejects it before it gets here).

# ponytail: assumes a single shared market calendar across all instruments
# (NSE/BSE both trade the same days) -- "yesterday" for crossover detection
# is computed once as MAX(trade_date) < as_of_date across the whole
# daily_prices table, not per-instrument. Fine while every instrument is
# ingested from the same daily bhavcopy run; if per-instrument trading
# calendars ever diverge, upgrade to a per-instrument LATERAL join instead.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.models import DailyPrice, Fundamental, Indicator, Instrument
from app.schemas.screen import (
    Between,
    Compare,
    CrossedAbove,
    CrossedBelow,
    FieldRef,
    Group,
    In,
    Operand,
    ScreenRule,
)

# field name -> (table key, column name). Table keys are resolved to actual
# joined tables/aliases per-query in compile_screen(). "close" deliberately
# maps to adjusted_close, never raw close -- CLAUDE.md architecture principle
# #4: screens must never compare against a corporate-action-unadjusted price.
FIELD_COLUMNS: dict[str, tuple[str, str]] = {
    "open": ("dp", "open"),
    "high": ("dp", "high"),
    "low": ("dp", "low"),
    "close": ("dp", "adjusted_close"),
    "volume": ("dp", "volume"),
    "sma_20": ("ind", "sma_20"),
    "sma_50": ("ind", "sma_50"),
    "sma_100": ("ind", "sma_100"),
    "sma_200": ("ind", "sma_200"),
    "ema_20": ("ind", "ema_20"),
    "ema_50": ("ind", "ema_50"),
    "rsi_14": ("ind", "rsi_14"),
    "macd": ("ind", "macd"),
    "macd_signal": ("ind", "macd_signal"),
    "macd_histogram": ("ind", "macd_histogram"),
    "atr_14": ("ind", "atr_14"),
    "volume_sma_20": ("ind", "volume_sma_20"),
    "high_52w": ("ind", "high_52w"),
    "low_52w": ("ind", "low_52w"),
    "pe": ("fund", "pe"),
    "pb": ("fund", "pb"),
    "roce": ("fund", "roce"),
    "debt_to_equity": ("fund", "debt_to_equity"),
    "market_cap": ("fund", "market_cap"),
    "sector": ("inst", "sector"),
    "industry": ("inst", "industry"),
    "exchange": ("inst", "exchange"),
    "series": ("inst", "series"),
}

# columns selected by compile_screen() that describe the match itself, not a
# screened field value -- callers writing an alerts snapshot (or an API match
# response) exclude these and keep everything else. "sector"/"close"/
# "prev_close" are also always-present display columns (see compile_screen)
# rather than screened values, so alert snapshots exclude them too.
NON_SNAPSHOT_COLUMNS = {"instrument_id", "symbol", "exchange", "trade_date", "sector", "close", "prev_close"}

COMPARE_OPS: dict[str, Callable[[ColumnElement, object], ColumnElement]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
}


def latest_trade_date(db: Session) -> date | None:
    return db.execute(select(func.max(DailyPrice.trade_date))).scalar_one_or_none()


def previous_trade_date(db: Session, before: date) -> date | None:
    return db.execute(select(func.max(DailyPrice.trade_date)).where(DailyPrice.trade_date < before)).scalar_one_or_none()


def _latest_fundamentals_subquery(as_of_date: date):
    """One row per instrument: its most recent fundamentals snapshot as of
    as_of_date (manual-entry data, so "most recent" may be months old)."""
    return (
        select(Fundamental)
        .where(Fundamental.as_of_date <= as_of_date)
        .distinct(Fundamental.instrument_id)
        .order_by(Fundamental.instrument_id, Fundamental.as_of_date.desc())
        .subquery("scr_fund")
    )


def _resolve(alias_map: dict, field: str) -> ColumnElement:
    table_key, col_name = FIELD_COLUMNS[field]
    return getattr(alias_map[table_key], col_name)


def _operand_expr(operand: Operand, today_map: dict, prev_map: dict) -> ColumnElement | float | int:
    if isinstance(operand, FieldRef):
        alias_map = prev_map if operand.when == "yesterday" else today_map
        col = _resolve(alias_map, operand.field)
        return col * operand.multiplier if operand.multiplier != 1 else col
    return operand


def _collect_fields(rule: ScreenRule) -> tuple[set[str], set[str]]:
    """(today_fields, prev_fields) referenced anywhere in the rule tree --
    used both to decide whether the "yesterday" join is needed and to select
    every value that should land in the alert snapshot."""
    today: set[str] = set()
    prev: set[str] = set()

    def walk(node: ScreenRule) -> None:
        if isinstance(node, Group):
            for child in node.rules:
                walk(child)
            return
        today.add(node.field)
        if isinstance(node, (Compare, CrossedAbove, CrossedBelow)) and isinstance(node.value, FieldRef):
            (prev if node.value.when == "yesterday" else today).add(node.value.field)
        if isinstance(node, (CrossedAbove, CrossedBelow)):
            prev.add(node.field)
            if isinstance(node.value, FieldRef):
                prev.add(node.value.field)

    walk(rule)
    return today, prev


def _compile(rule: ScreenRule, today_map: dict, prev_map: dict) -> ColumnElement:
    if isinstance(rule, Group):
        parts = [_compile(child, today_map, prev_map) for child in rule.rules]
        return and_(*parts) if rule.op == "and" else or_(*parts)

    if isinstance(rule, Compare):
        col = _resolve(today_map, rule.field)
        return COMPARE_OPS[rule.op](col, _operand_expr(rule.value, today_map, prev_map))

    if isinstance(rule, Between):
        col = _resolve(today_map, rule.field)
        return col.between(rule.low, rule.high)

    if isinstance(rule, In):
        col = _resolve(today_map, rule.field)
        return col.in_(rule.values)

    if isinstance(rule, (CrossedAbove, CrossedBelow)):
        today_col = _resolve(today_map, rule.field)
        today_val = _operand_expr(rule.value, today_map, prev_map)
        prev_col = _resolve(prev_map, rule.field)
        prev_val = _operand_expr(rule.value, prev_map, prev_map)
        if isinstance(rule, CrossedAbove):
            return and_(prev_col <= prev_val, today_col > today_val)
        return and_(prev_col >= prev_val, today_col < today_val)

    raise TypeError(f"unhandled rule type: {type(rule)!r}")  # unreachable given ScreenRule's union


def compile_screen(rule: ScreenRule, as_of_date: date, prev_date: date | None) -> Select | None:
    """Build the SELECT that returns one row per matching instrument, with
    instrument_id/symbol/exchange/trade_date, always-present display columns
    (sector/close/prev_close -- for the API's Sector/Price/Day-change
    columns regardless of what the rule itself screens on), plus one labeled
    column per field referenced in the rule (the match snapshot). Returns
    None if the rule needs a "yesterday" comparison but there is no earlier
    trading day to compare against yet.
    """
    today_fields, prev_fields = _collect_fields(rule)
    if bool(prev_fields) and prev_date is None:
        return None

    dp = aliased(DailyPrice, name="scr_dp")
    ind = aliased(Indicator, name="scr_ind")
    fund_sq = _latest_fundamentals_subquery(as_of_date)
    today_map = {"dp": dp, "ind": ind, "fund": fund_sq.c, "inst": Instrument}

    stmt = (
        select(
            Instrument.id.label("instrument_id"),
            Instrument.symbol,
            Instrument.exchange,
            dp.trade_date,
        )
        .select_from(Instrument)
        .join(dp, and_(dp.instrument_id == Instrument.id, dp.trade_date == as_of_date))
        .outerjoin(ind, and_(ind.instrument_id == Instrument.id, ind.trade_date == as_of_date))
        .outerjoin(fund_sq, fund_sq.c.instrument_id == Instrument.id)
    )

    # Prev-day join happens whenever a prior trading day exists, not only
    # when the rule needs a yesterday comparison -- day-change display needs
    # it unconditionally, and it's a cheap indexed outerjoin either way.
    prev_map: dict = {}
    dp_prev = None
    if prev_date is not None:
        dp_prev = aliased(DailyPrice, name="scr_dp_prev")
        ind_prev = aliased(Indicator, name="scr_ind_prev")
        stmt = stmt.outerjoin(
            dp_prev, and_(dp_prev.instrument_id == Instrument.id, dp_prev.trade_date == prev_date)
        ).outerjoin(ind_prev, and_(ind_prev.instrument_id == Instrument.id, ind_prev.trade_date == prev_date))
        prev_map = {"dp": dp_prev, "ind": ind_prev}

    label_cols: dict[str, ColumnElement] = {"sector": Instrument.sector, "close": dp.adjusted_close}
    if dp_prev is not None:
        label_cols["prev_close"] = dp_prev.adjusted_close
    for field in sorted(today_fields):
        label_cols.setdefault(field, _resolve(today_map, field))

    stmt = stmt.add_columns(*[col.label(label) for label, col in label_cols.items()])
    stmt = stmt.where(_compile(rule, today_map, prev_map))
    return stmt
