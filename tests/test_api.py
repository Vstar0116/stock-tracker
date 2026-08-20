"""API tests: happy path plus ownership enforcement, for every /api/* router.

Run with: pytest tests/test_api.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, using a fresh
throwaway instrument so nothing here depends on (or mutates) real market data.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import engine, get_db
from app.main import app
from app.models import DailyPrice, Indicator, Instrument, User
from app.security import create_access_token, hash_password
from app.services.screening import latest_trade_date, previous_trade_date


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


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def owner(db):
    u = User(email="owner@example.com", name="Owner", password_hash=hash_password("x"), is_active=True)
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def other_user(db):
    u = User(email="other@example.com", name="Other", password_hash=hash_password("x"), is_active=True)
    db.add(u)
    db.flush()
    return u


def _auth(user: User) -> dict:
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def instrument(db):
    inst = Instrument(symbol="APITEST", exchange="NSE", company_name="Api Test Co", sector="IT", is_active=True)
    db.add(inst)
    db.flush()
    return inst


@pytest.fixture()
def today(db):
    d = latest_trade_date(db)
    assert d is not None, "expected the dev DB to already have some daily_prices"
    return d


@pytest.fixture()
def prev_day(db, today):
    d = previous_trade_date(db, today)
    assert d is not None, "expected the dev DB to have more than one trade_date"
    return d


@pytest.fixture()
def priced_instrument(db, instrument, today, prev_day):
    """The test instrument with two days of price+indicator history, dated to
    line up with the DB's real current "latest trade date" -- the screening
    and status endpoints derive as_of_date from the whole table, so a test
    row has to land on that same date to be seen by them."""
    for d, close in ((prev_day, "95.00"), (today, "105.00")):
        price = Decimal(close)
        db.add(
            DailyPrice(
                instrument_id=instrument.id, trade_date=d, open=price, high=price, low=price,
                close=price, adjusted_close=price, volume=1000,
            )
        )
        db.add(
            Indicator(
                instrument_id=instrument.id, trade_date=d,
                sma_50=Decimal("90"), sma_200=Decimal("80"), rsi_14=Decimal("55"),
            )
        )
    db.flush()
    return instrument


class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/instruments"),
            ("get", "/api/watchlists"),
            ("get", "/api/screens"),
            ("get", "/api/alerts"),
            ("get", "/api/status"),
        ],
    )
    def test_no_token_rejected(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401


class TestInstruments:
    def test_search_and_paginate(self, client, owner, priced_instrument):
        resp = client.get("/api/instruments", params={"q": "APITEST", "limit": 10}, headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert any(i["symbol"] == "APITEST" for i in body["items"])

    def test_detail_includes_latest_indicators(self, client, owner, priced_instrument, today):
        resp = client.get(f"/api/instruments/{priced_instrument.id}", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "APITEST"
        assert body["latest_indicators"]["trade_date"] == today.isoformat()
        assert body["latest_indicators"]["sma_50"] == 90.0

    def test_detail_404_for_unknown_instrument(self, client, owner):
        assert client.get("/api/instruments/999999999", headers=_auth(owner)).status_code == 404

    def test_prices_date_range(self, client, owner, priced_instrument, today):
        resp = client.get(
            f"/api/instruments/{priced_instrument.id}/prices",
            params={"from": today.isoformat(), "to": today.isoformat()},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["close"] == 105.0


class TestWatchlists:
    def test_crud_items_and_view(self, client, owner, priced_instrument, today):
        headers = _auth(owner)

        create = client.post("/api/watchlists", json={"name": "My List"}, headers=headers)
        assert create.status_code == 201
        wl_id = create.json()["id"]

        listed = client.get("/api/watchlists", headers=headers)
        assert any(w["id"] == wl_id for w in listed.json()["items"])

        add = client.post(
            f"/api/watchlists/{wl_id}/items",
            json={"instrument_id": priced_instrument.id, "notes": "watch this"},
            headers=headers,
        )
        assert add.status_code == 201

        dup = client.post(f"/api/watchlists/{wl_id}/items", json={"instrument_id": priced_instrument.id}, headers=headers)
        assert dup.status_code == 409

        view = client.get(f"/api/watchlists/{wl_id}/view", headers=headers)
        assert view.status_code == 200
        rows = view.json()["items"]
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "APITEST"
        assert row["trade_date"] == today.isoformat()
        assert row["close"] == 105.0
        assert row["indicators"]["sma_200"] == 80.0
        assert row["trend_state"] == "uptrend"  # close 105 > sma_50 90 > sma_200 80
        assert row["notes"] == "watch this"

        rename = client.patch(f"/api/watchlists/{wl_id}", json={"name": "Renamed"}, headers=headers)
        assert rename.status_code == 200
        assert rename.json()["name"] == "Renamed"

        remove = client.delete(f"/api/watchlists/{wl_id}/items/{priced_instrument.id}", headers=headers)
        assert remove.status_code == 204
        assert client.delete(f"/api/watchlists/{wl_id}/items/{priced_instrument.id}", headers=headers).status_code == 404

        assert client.delete(f"/api/watchlists/{wl_id}", headers=headers).status_code == 204

    def test_ownership_enforced(self, client, owner, other_user):
        create = client.post("/api/watchlists", json={"name": "Private"}, headers=_auth(owner))
        wl_id = create.json()["id"]

        other_headers = _auth(other_user)
        listed = client.get("/api/watchlists", headers=other_headers)
        assert all(w["id"] != wl_id for w in listed.json()["items"])

        assert client.get(f"/api/watchlists/{wl_id}/view", headers=other_headers).status_code == 404
        assert client.patch(f"/api/watchlists/{wl_id}", json={"name": "Hijacked"}, headers=other_headers).status_code == 404
        assert client.delete(f"/api/watchlists/{wl_id}", headers=other_headers).status_code == 404


class TestScreens:
    def test_create_rejects_unknown_field(self, client, owner):
        resp = client.post(
            "/api/screens",
            json={"name": "Bad", "definition": {"type": "compare", "op": "gt", "field": "not_a_real_field", "value": 1}},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_create_preview_run_update_delete(self, client, owner, priced_instrument, today):
        headers = _auth(owner)
        rule = {"type": "compare", "op": "gt", "field": "close", "value": 100}

        create = client.post("/api/screens", json={"name": "Close > 100", "definition": rule}, headers=headers)
        assert create.status_code == 201
        screen_id = create.json()["id"]
        assert any(s["id"] == screen_id for s in client.get("/api/screens", headers=headers).json()["items"])

        preview = client.post("/api/screens/preview", json={"definition": rule}, headers=headers)
        assert preview.status_code == 200
        assert any(m["instrument_id"] == priced_instrument.id for m in preview.json()["items"])

        run = client.post(f"/api/screens/{screen_id}/run", headers=headers)
        assert run.status_code == 200
        assert any(m["instrument_id"] == priced_instrument.id for m in run.json()["items"])

        # idempotent: running again doesn't error and still reports the match
        run_again = client.post(f"/api/screens/{screen_id}/run", headers=headers)
        assert run_again.status_code == 200
        assert any(m["instrument_id"] == priced_instrument.id for m in run_again.json()["items"])

        update = client.patch(f"/api/screens/{screen_id}", json={"is_active": False}, headers=headers)
        assert update.status_code == 200
        assert update.json()["is_active"] is False

        assert client.delete(f"/api/screens/{screen_id}", headers=headers).status_code == 204

    def test_ownership_enforced(self, client, owner, other_user):
        rule = {"type": "compare", "op": "gt", "field": "close", "value": 100}
        create = client.post("/api/screens", json={"name": "Private Screen", "definition": rule}, headers=_auth(owner))
        screen_id = create.json()["id"]

        other_headers = _auth(other_user)
        assert client.patch(f"/api/screens/{screen_id}", json={"name": "Hijack"}, headers=other_headers).status_code == 404
        assert client.post(f"/api/screens/{screen_id}/run", headers=other_headers).status_code == 404
        assert client.delete(f"/api/screens/{screen_id}", headers=other_headers).status_code == 404


class TestAlerts:
    def test_list_filter_mark_seen_and_ownership(self, client, owner, other_user, priced_instrument):
        headers = _auth(owner)
        rule = {"type": "compare", "op": "gt", "field": "close", "value": 100}
        screen_id = client.post("/api/screens", json={"name": "Alert Source", "definition": rule}, headers=headers).json()["id"]
        run = client.post(f"/api/screens/{screen_id}/run", headers=headers)
        assert run.json()["items"], "expected the screen to match the seeded instrument"

        listed = client.get("/api/alerts", headers=headers)
        assert listed.status_code == 200
        alerts = listed.json()["items"]
        alert = next(a for a in alerts if a["instrument_id"] == priced_instrument.id)
        assert alert["seen"] is False
        assert alert["screen_id"] == screen_id

        filtered = client.get("/api/alerts", params={"screen_id": screen_id, "seen": False}, headers=headers)
        assert any(a["id"] == alert["id"] for a in filtered.json()["items"])

        other_headers = _auth(other_user)
        other_listed = client.get("/api/alerts", headers=other_headers)
        assert all(a["id"] != alert["id"] for a in other_listed.json()["items"])
        assert client.post(f"/api/alerts/{alert['id']}/seen", headers=other_headers).status_code == 404

        mark = client.post(f"/api/alerts/{alert['id']}/seen", headers=headers)
        assert mark.status_code == 200
        assert mark.json()["seen"] is True


class TestStatus:
    def test_returns_freshness_info(self, client, owner):
        resp = client.get("/api/status", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"latest_trade_date", "expected_trade_date", "is_current", "last_pipeline_run_at", "last_pipeline_status"}
