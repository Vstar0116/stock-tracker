"""Pydantic models for a screen's rule tree (`screens.definition` JSONB).

Grammar (every node is tagged by its `type` field, a discriminated union):

    ScreenRule := Compare | Between | In | CrossedAbove | CrossedBelow | Group
    Group      := {"type": "group", "op": "and"|"or", "rules": [ScreenRule, ...]}

Examples (as JSON, matching the task's examples):

    close > 100                        {"type": "compare", "op": "gt", "field": "close", "value": 100}
    close > sma_200                    {"type": "compare", "op": "gt", "field": "close",
                                         "value": {"field": "sma_200"}}
    volume > volume_sma_20 * 3         {"type": "compare", "op": "gt", "field": "volume",
                                         "value": {"field": "volume_sma_20", "multiplier": 3}}
    sma_50 crossed above sma_200       {"type": "crossed_above", "field": "sma_50",
                                         "value": {"field": "sma_200"}}
    rsi_14 between 30 and 70           {"type": "between", "field": "rsi_14", "low": 30, "high": 70}
    sector in ('Pharma', 'IT')         {"type": "in", "field": "sector", "values": ["Pharma", "IT"]}
    close > yesterday's close           {"type": "compare", "op": "gt", "field": "close",
                                         "value": {"field": "close", "when": "yesterday"}}
    A and (B or C)                     {"type": "group", "op": "and", "rules": [A, {"type": "group",
                                         "op": "or", "rules": [B, C]}]}

A `value` operand that's a field reference can point at yesterday's row
instead of today's via `"when": "yesterday"` (default "today") -- this is
what makes a plain "price up on the day" screen expressible without a
degenerate self-crossover. Only allowed on DAILY_NUMERIC_FIELDS (the
`daily_prices`/`indicators` tables have a "yesterday" row; fundamentals
don't).

Every leaf `field` name is validated here against a fixed registry -- an
unknown field name (typo, or an attempt to reference something outside the
allowed set) fails validation before the definition is ever stored or
compiled to SQL. services/screening.py uses these same field sets to build
the query, so a name that passes here is guaranteed to have a column mapping
there.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

# daily_prices + indicators -- true daily time series, so "yesterday" is a
# meaningful comparison and crossover detection is allowed on these.
DAILY_NUMERIC_FIELDS = {
    "open",
    "high",
    "low",
    "close",  # maps to daily_prices.adjusted_close, never raw close -- see screening.py
    "volume",
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
}
# fundamentals -- manually entered, irregular snapshots, not a daily series.
# Usable in compare/between but not crossover ("crossed above" implies a
# yesterday-vs-today relationship that doesn't make sense for a value that
# might not change for months).
FUNDAMENTAL_FIELDS = {"pe", "pb", "roce", "debt_to_equity", "market_cap"}

NUMERIC_FIELDS = DAILY_NUMERIC_FIELDS | FUNDAMENTAL_FIELDS
CATEGORICAL_FIELDS = {"sector", "industry", "exchange", "series"}
CROSSABLE_FIELDS = DAILY_NUMERIC_FIELDS


def _check_field(allowed: set[str], v: str) -> str:
    if v not in allowed:
        raise ValueError(f"unknown or disallowed field: {v!r} (allowed: {sorted(allowed)})")
    return v


class FieldRef(BaseModel):
    """A reference to another numeric field, optionally scaled -- e.g. the
    `volume_sma_20 * 3` half of `volume > volume_sma_20 * 3` -- and optionally
    pointing at yesterday's row instead of today's (`when="yesterday"`), e.g.
    the `yesterday's close` half of a "price up on the day" screen."""

    field: str
    multiplier: float = 1.0
    when: Literal["today", "yesterday"] = "today"

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(NUMERIC_FIELDS, v)

    @model_validator(mode="after")
    def _yesterday_requires_daily_field(self) -> FieldRef:
        if self.when == "yesterday" and self.field not in CROSSABLE_FIELDS:
            raise ValueError(f"{self.field!r} has no daily history to compare against yesterday")
        return self


Operand = Union[float, int, FieldRef]


class Compare(BaseModel):
    """field <op> constant, or field <op> another field (optionally scaled)."""

    type: Literal["compare"] = "compare"
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    field: str
    value: Operand

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(NUMERIC_FIELDS, v)


class Between(BaseModel):
    type: Literal["between"] = "between"
    field: str
    low: float
    high: float

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(NUMERIC_FIELDS, v)

    @model_validator(mode="after")
    def _low_not_above_high(self) -> Between:
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) must be <= high ({self.high})")
        return self


class In(BaseModel):
    type: Literal["in"] = "in"
    field: str
    values: list[str] = Field(min_length=1)

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(CATEGORICAL_FIELDS, v)


class _CrossedBase(BaseModel):
    field: str
    value: Operand

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(CROSSABLE_FIELDS, v)

    @field_validator("value")
    @classmethod
    def _crossable_value(cls, v: Operand) -> Operand:
        if isinstance(v, FieldRef):
            _check_field(CROSSABLE_FIELDS, v.field)
            if v.when != "today":
                # crossed_above/below already evaluates `value` on both today's
                # and yesterday's row itself -- an explicit "yesterday" here
                # would mean "the day before yesterday", which isn't supported.
                raise ValueError('crossover value must use when="today" (the default)')
        return v


class CrossedAbove(_CrossedBase):
    """Yesterday `field` <= `value`, and today `field` > `value`."""

    type: Literal["crossed_above"] = "crossed_above"


class CrossedBelow(_CrossedBase):
    """Yesterday `field` >= `value`, and today `field` < `value`."""

    type: Literal["crossed_below"] = "crossed_below"


class Group(BaseModel):
    type: Literal["group"] = "group"
    op: Literal["and", "or"]
    rules: list[ScreenRule] = Field(min_length=1)


ScreenRule = Annotated[
    Union[Compare, Between, In, CrossedAbove, CrossedBelow, Group],
    Field(discriminator="type"),
]
Group.model_rebuild()

ScreenRuleAdapter: TypeAdapter[ScreenRule] = TypeAdapter(ScreenRule)


def parse_screen_definition(data: dict) -> ScreenRule:
    """Validate a screen's raw JSONB `definition` into a rule tree. Raises
    pydantic.ValidationError (unknown field names, malformed shape, etc.)."""
    return ScreenRuleAdapter.validate_python(data)


# ---------------------------------------------------------------------------
# API request/response shapes (app/api/screens.py). `definition` is typed as
# ScreenRule directly, so FastAPI validates the rule tree as part of normal
# request parsing -- an unknown field or malformed node 422s automatically,
# no separate validation step needed at the route.
# ---------------------------------------------------------------------------


class ScreenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    definition: ScreenRule
    is_active: bool = True


class ScreenUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    definition: ScreenRule | None = None
    is_active: bool | None = None


class ScreenOut(BaseModel):
    id: int
    name: str
    description: str | None
    definition: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScreenPreviewRequest(BaseModel):
    definition: ScreenRule


class ScreenFromTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ScreenFromTextResponse(BaseModel):
    definition: ScreenRule


class ScreenMatchOut(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    sector: str | None
    trade_date: date
    close: float | None
    day_change_pct: float | None
    values: dict
