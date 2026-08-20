"""Tests for the screening engine: rule-tree validation (app/schemas/screen.py)
and SQL compilation (app/services/screening.py).

Run with: pytest tests/test_screening.py -v
Requires the local Postgres (docker compose up -d) for the compiler tests --
each runs inside a SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs.run_screens import evaluate_screen
from app.models import Alert, DailyPrice, Indicator, Instrument, Screen, User
from app.schemas.screen import parse_screen_definition
from app.services.screening import compile_screen

TODAY = date(2026, 6, 20)
PREV = date(2026, 6, 19)


# ---------------------------------------------------------------------------
# schema validation -- no DB needed
# ---------------------------------------------------------------------------


class TestScreenSchemaValidation:
    def test_rejects_unknown_field_in_compare(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "compare", "op": "gt", "field": "not_a_real_field", "value": 100})

    def test_rejects_unknown_field_in_between(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "between", "field": "bogus", "low": 30, "high": 70})

    def test_rejects_unknown_field_in_in(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "in", "field": "bogus", "values": ["Pharma"]})

    def test_rejects_categorical_field_in_numeric_compare(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "compare", "op": "gt", "field": "sector", "value": 1})

    def test_rejects_fundamental_field_in_crossover(self):
        # pe/pb/etc aren't a daily series -- "crossed above" isn't meaningful.
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "crossed_above", "field": "pe", "value": 20})

    def test_rejects_unknown_field_in_crossover_value(self):
        with pytest.raises(ValidationError):
            parse_screen_definition(
                {"type": "crossed_above", "field": "sma_50", "value": {"field": "not_real"}}
            )

    def test_rejects_between_low_above_high(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "between", "field": "rsi_14", "low": 70, "high": 30})

    def test_rejects_empty_group(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "group", "op": "and", "rules": []})

    def test_rejects_malformed_missing_field(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "compare", "op": "gt", "value": 100})

    def test_rejects_unknown_op(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "compare", "op": "startswith", "field": "close", "value": 100})

    def test_parses_nested_and_or(self):
        rule = parse_screen_definition(
            {
                "type": "group",
                "op": "and",
                "rules": [
                    {"type": "compare", "op": "gt", "field": "close", "value": 100},
                    {
                        "type": "group",
                        "op": "or",
                        "rules": [
                            {"type": "in", "field": "sector", "values": ["Pharma", "IT"]},
                            {"type": "between", "field": "rsi_14", "low": 30, "high": 70},
                        ],
                    },
                ],
            }
        )
        assert rule.type == "group"
        assert rule.op == "and"
        assert len(rule.rules) == 2

    def test_rejects_yesterday_ref_on_fundamental_field(self):
        with pytest.raises(ValidationError):
            parse_screen_definition({"type": "compare", "op": "gt", "field": "pe", "value": {"field": "pe", "when": "yesterday"}})

    def test_rejects_yesterday_ref_inside_crossover_value(self):
        with pytest.raises(ValidationError):
            parse_screen_definition(
                {"type": "crossed_above", "field": "sma_50", "value": {"field": "sma_200", "when": "yesterday"}}
            )

    def test_parses_price_up_on_the_day(self):
        rule = parse_screen_definition(
            {"type": "compare", "op": "gt", "field": "close", "value": {"field": "close", "when": "yesterday"}}
        )
        assert rule.value.when == "yesterday"

    def test_parses_percentage_comparison(self):
        rule = parse_screen_definition(
            {"type": "compare", "op": "gt", "field": "volume", "value": {"field": "volume_sma_20", "multiplier": 3}}
        )
        assert rule.value.field == "volume_sma_20"
        assert rule.value.multiplier == 3


# ---------------------------------------------------------------------------
# SQL compilation + execution against real Postgres
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _make_instrument(db: Session, symbol: str, sector: str | None = None) -> Instrument:
    inst = Instrument(symbol=symbol, exchange="NSE", company_name=f"{symbol} Ltd", sector=sector, is_active=True)
    db.add(inst)
    db.flush()
    return inst


def _add_price(db: Session, instrument: Instrument, trade_date: date, close, volume: int = 100_000) -> DailyPrice:
    close_dec = Decimal(str(close))
    price = DailyPrice(
        instrument_id=instrument.id,
        trade_date=trade_date,
        open=close_dec,
        high=close_dec,
        low=close_dec,
        close=close_dec,
        adjusted_close=close_dec,
        volume=volume,
    )
    db.add(price)
    db.flush()
    return price


def _add_indicator(db: Session, instrument: Instrument, trade_date: date, **fields) -> Indicator:
    values = {k: (Decimal(str(v)) if v is not None else None) for k, v in fields.items()}
    ind = Indicator(instrument_id=instrument.id, trade_date=trade_date, **values)
    db.add(ind)
    db.flush()
    return ind


def _run(db: Session, rule_dict: dict, as_of_date: date = TODAY, prev_date: date | None = PREV) -> list[dict]:
    rule = parse_screen_definition(rule_dict)
    stmt = compile_screen(rule, as_of_date, prev_date)
    if stmt is None:
        return []
    return [dict(row) for row in db.execute(stmt).mappings().all()]


class TestCompareConstant:
    def test_close_greater_than_constant(self, db):
        winner = _make_instrument(db, "WINNER")
        loser = _make_instrument(db, "LOSER")
        _add_price(db, winner, TODAY, "150.00")
        _add_price(db, loser, TODAY, "50.00")

        matches = _run(db, {"type": "compare", "op": "gt", "field": "close", "value": 100})

        assert {m["symbol"] for m in matches} == {"WINNER"}
        assert matches[0]["close"] == Decimal("150.00")


class TestCompareField:
    def test_close_greater_than_another_field(self, db):
        above = _make_instrument(db, "ABOVE")
        below = _make_instrument(db, "BELOW")
        _add_price(db, above, TODAY, "220.00")
        _add_indicator(db, above, TODAY, sma_200="200.00")
        _add_price(db, below, TODAY, "180.00")
        _add_indicator(db, below, TODAY, sma_200="200.00")

        matches = _run(db, {"type": "compare", "op": "gt", "field": "close", "value": {"field": "sma_200"}})

        assert {m["symbol"] for m in matches} == {"ABOVE"}


class TestPercentageComparison:
    def test_volume_above_3x_its_average(self, db):
        spike = _make_instrument(db, "SPIKE")
        normal = _make_instrument(db, "NORMAL")
        _add_price(db, spike, TODAY, "100.00", volume=400_000)
        _add_indicator(db, spike, TODAY, volume_sma_20="100000")
        _add_price(db, normal, TODAY, "100.00", volume=150_000)
        _add_indicator(db, normal, TODAY, volume_sma_20="100000")

        matches = _run(
            db, {"type": "compare", "op": "gt", "field": "volume", "value": {"field": "volume_sma_20", "multiplier": 3}}
        )

        assert {m["symbol"] for m in matches} == {"SPIKE"}


class TestBetween:
    def test_rsi_range_filter(self, db):
        mid = _make_instrument(db, "MID")
        overbought = _make_instrument(db, "HOT")
        _add_price(db, mid, TODAY, "100.00")
        _add_indicator(db, mid, TODAY, rsi_14="55.00")
        _add_price(db, overbought, TODAY, "100.00")
        _add_indicator(db, overbought, TODAY, rsi_14="90.00")

        matches = _run(db, {"type": "between", "field": "rsi_14", "low": 30, "high": 70})

        assert {m["symbol"] for m in matches} == {"MID"}


class TestCategoricalIn:
    def test_sector_in_list(self, db):
        pharma = _make_instrument(db, "PHARMACO", sector="Pharma")
        bank = _make_instrument(db, "BANKCO", sector="Banking")
        _add_price(db, pharma, TODAY, "100.00")
        _add_price(db, bank, TODAY, "100.00")

        matches = _run(db, {"type": "in", "field": "sector", "values": ["Pharma", "IT"]})

        assert {m["symbol"] for m in matches} == {"PHARMACO"}


class TestCrossover:
    def test_golden_cross_sma50_above_sma200(self, db):
        crosser = _make_instrument(db, "CROSSER")
        _add_price(db, crosser, PREV, "195.00")
        _add_indicator(db, crosser, PREV, sma_50="199.00", sma_200="200.00")  # below yesterday
        _add_price(db, crosser, TODAY, "205.00")
        _add_indicator(db, crosser, TODAY, sma_50="201.00", sma_200="200.00")  # above today

        already_above = _make_instrument(db, "ALREADY")
        _add_price(db, already_above, PREV, "210.00")
        _add_indicator(db, already_above, PREV, sma_50="205.00", sma_200="200.00")  # already above yesterday
        _add_price(db, already_above, TODAY, "212.00")
        _add_indicator(db, already_above, TODAY, sma_50="206.00", sma_200="200.00")

        never_crosses = _make_instrument(db, "NEVER")
        _add_price(db, never_crosses, PREV, "150.00")
        _add_indicator(db, never_crosses, PREV, sma_50="140.00", sma_200="200.00")
        _add_price(db, never_crosses, TODAY, "155.00")
        _add_indicator(db, never_crosses, TODAY, sma_50="142.00", sma_200="200.00")

        matches = _run(db, {"type": "crossed_above", "field": "sma_50", "value": {"field": "sma_200"}})

        assert {m["symbol"] for m in matches} == {"CROSSER"}

    def test_crossed_below(self, db):
        crosser = _make_instrument(db, "DEADCROSS")
        _add_price(db, crosser, PREV, "205.00")
        _add_indicator(db, crosser, PREV, sma_50="201.00", sma_200="200.00")
        _add_price(db, crosser, TODAY, "195.00")
        _add_indicator(db, crosser, TODAY, sma_50="199.00", sma_200="200.00")

        matches = _run(db, {"type": "crossed_below", "field": "sma_50", "value": {"field": "sma_200"}})

        assert {m["symbol"] for m in matches} == {"DEADCROSS"}

    def test_no_prior_trading_day_returns_no_matches_not_an_error(self, db):
        crosser = _make_instrument(db, "SOLO")
        _add_price(db, crosser, TODAY, "205.00")
        _add_indicator(db, crosser, TODAY, sma_50="201.00", sma_200="200.00")

        matches = _run(
            db,
            {"type": "crossed_above", "field": "sma_50", "value": {"field": "sma_200"}},
            as_of_date=TODAY,
            prev_date=None,
        )

        assert matches == []


class TestNestedAndOr:
    def test_price_up_and_volume_spike(self, db):
        # "volume above 3x its 20-day average, AND price up on the day"
        both = _make_instrument(db, "BOTH")
        _add_price(db, both, PREV, "100.00")
        _add_price(db, both, TODAY, "105.00", volume=500_000)
        _add_indicator(db, both, TODAY, volume_sma_20="100000")

        volume_only = _make_instrument(db, "VOLONLY")
        _add_price(db, volume_only, PREV, "100.00")
        _add_price(db, volume_only, TODAY, "95.00", volume=500_000)  # price down
        _add_indicator(db, volume_only, TODAY, volume_sma_20="100000")

        price_only = _make_instrument(db, "PRICEONLY")
        _add_price(db, price_only, PREV, "100.00")
        _add_price(db, price_only, TODAY, "105.00", volume=150_000)  # no volume spike
        _add_indicator(db, price_only, TODAY, volume_sma_20="100000")

        rule = {
            "type": "group",
            "op": "and",
            "rules": [
                {"type": "compare", "op": "gt", "field": "volume", "value": {"field": "volume_sma_20", "multiplier": 3}},
                # "price up on the day" == today's close > yesterday's close.
                {"type": "compare", "op": "gt", "field": "close", "value": {"field": "close", "when": "yesterday"}},
            ],
        }
        matches = _run(db, rule)

        assert {m["symbol"] for m in matches} == {"BOTH"}

    def test_nested_or_inside_and(self, db):
        it_stock = _make_instrument(db, "ITCO", sector="IT")
        pharma_stock = _make_instrument(db, "PHARMACO2", sector="Pharma")
        bank_stock = _make_instrument(db, "BANKCO2", sector="Banking")
        for inst in (it_stock, pharma_stock, bank_stock):
            _add_price(db, inst, TODAY, "150.00")

        rule = {
            "type": "group",
            "op": "and",
            "rules": [
                {"type": "compare", "op": "gt", "field": "close", "value": 100},
                {
                    "type": "group",
                    "op": "or",
                    "rules": [
                        {"type": "in", "field": "sector", "values": ["Pharma", "IT"]},
                    ],
                },
            ],
        }
        matches = _run(db, rule)

        assert {m["symbol"] for m in matches} == {"ITCO", "PHARMACO2"}


class TestEvaluateScreenIdempotency:
    def test_rerunning_does_not_duplicate_alerts(self, db):
        inst = _make_instrument(db, "REPEATCO")
        _add_price(db, inst, TODAY, "150.00")

        user = User(email="screen-test@example.com", name="Screen Test", password_hash="x")
        db.add(user)
        db.flush()
        screen = Screen(
            user_id=user.id,
            name="close over 100",
            definition={"type": "compare", "op": "gt", "field": "close", "value": 100},
            is_active=True,
        )
        db.add(screen)
        db.flush()

        first = evaluate_screen(db, screen, TODAY, PREV)
        second = evaluate_screen(db, screen, TODAY, PREV)

        assert first == 1
        assert second == 0  # already alerted for (screen, instrument, date) -- no duplicate
        assert db.query(Alert).filter_by(screen_id=screen.id).count() == 1
