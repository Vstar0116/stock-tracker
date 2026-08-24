"""API tests: happy path plus ownership enforcement, for every /api/* router.

Run with: pytest tests/test_api.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, using a fresh
throwaway instrument so nothing here depends on (or mutates) real market data.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import rate_limit
from app.db.session import engine, get_db
from app.jobs.compute_indicators import load_price_history, upsert_indicators
from app.main import app
from app.models import DailyPrice, Instrument, JobRun, User
from app.schemas.screen import parse_screen_definition
from app.security import create_access_token, hash_password
from app.services.indicators import compute_all_indicators
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
def client(db, monkeypatch):
    def override_get_db():
        yield db

    def _session_factory():
        return Session(bind=db.connection(), join_transaction_mode="create_savepoint")

    # RateLimiter.check() opens its own SessionLocal() rather than using the
    # injected `db` -- same reasoning as test_auth.py's client fixture. Point
    # it at this test's SAVEPOINT-backed connection so any rate-limited route
    # (login, screens/from-text) never writes rows into the real dev database.
    monkeypatch.setattr(rate_limit, "SessionLocal", _session_factory)

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
def today():
    # Far-future so this always wins as MAX(trade_date) over latest_trade_date's
    # whole-table scan, regardless of whatever real data the dev DB has ingested.
    return date(2099, 1, 1)


@pytest.fixture()
def prev_day():
    return date(2098, 12, 31)


PRICE_HISTORY_BARS = 200  # sma_200 is the longest-lookback field these tests read (see app/services/indicators.py)


@pytest.fixture()
def priced_instrument(db, instrument, today, prev_day):
    """200 daily bars ending on `today` -- the full window sma_200 (the
    longest-lookback indicator these tests actually read) needs before it's
    non-NULL -- then runs the real compute_all_indicators()/upsert_indicators
    (app/jobs/compute_indicators.py) over them, so this exercises the actual
    indicator engine instead of hand-typed rows.

    Closes are picked so the real computation lands on exactly sma_50=90.0
    and sma_200=80.0: the last 50 closes sum to 4500, all 200 sum to 16000 --
    both divide evenly, so there's no float-rounding risk against the
    `== 90.0` / `== 80.0` assertions below.
    """
    closes = (
        [Decimal(100)] * 100  # bars 1-100
        + [Decimal(30)] * 50  # bars 101-150
        + [Decimal(90)] * 48  # bars 151-198
        + [Decimal(75)]  # bar 199 == prev_day
        + [Decimal(105)]  # bar 200 == today
    )
    assert len(closes) == PRICE_HISTORY_BARS
    assert sum(closes) == 16000 and sum(closes[-50:]) == 4500

    dates = [today - timedelta(days=PRICE_HISTORY_BARS - 1 - i) for i in range(PRICE_HISTORY_BARS)]
    assert dates[-1] == today and dates[-2] == prev_day

    for d, close in zip(dates, closes):
        db.add(
            DailyPrice(
                instrument_id=instrument.id, trade_date=d, open=close, high=close, low=close,
                close=close, adjusted_close=close, volume=1000,
            )
        )
    db.flush()

    prices = load_price_history(db, instrument.id)
    upsert_indicators(db, instrument.id, compute_all_indicators(prices))
    db.flush()
    return instrument


def test_latest_and_previous_trade_date(db, priced_instrument, today, prev_day):
    assert latest_trade_date(db) == today
    assert previous_trade_date(db, today) == prev_day


class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/instruments"),
            ("get", "/api/watchlists"),
            ("get", "/api/screens"),
            ("get", "/api/alerts"),
            ("get", "/api/status"),
            ("get", "/api/status/detail"),
        ],
    )
    def test_no_token_rejected(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    # Every mutating/stateful route, not just the GETs above -- proves the
    # auth dependency is actually wired on each one rather than assuming a
    # router-level Depends() (or its absence) covers routes added later.
    # instrument_id=1/watchlist_id=1/screen_id=1/alert_id=1 never need to
    # exist: with no token at all, auth is checked before any of these IDs
    # are looked up, so every case 401s regardless.
    @pytest.mark.parametrize(
        "method,path,json_body",
        [
            ("post", "/api/watchlists", {"name": "x"}),
            ("patch", "/api/watchlists/1", {"name": "x"}),
            ("delete", "/api/watchlists/1", None),
            ("post", "/api/watchlists/1/items", {"instrument_id": 1}),
            ("delete", "/api/watchlists/1/items/1", None),
            ("get", "/api/watchlists/1/view", None),
            ("post", "/api/screens", {"name": "x", "definition": {"type": "compare", "op": "gt", "field": "close", "value": 1}}),
            ("patch", "/api/screens/1", {"name": "x"}),
            ("delete", "/api/screens/1", None),
            ("post", "/api/screens/from-text", {"text": "close above 100"}),
            ("post", "/api/screens/preview", {"definition": {"type": "compare", "op": "gt", "field": "close", "value": 1}}),
            ("post", "/api/screens/1/run", None),
            ("post", "/api/alerts/1/seen", None),
            ("get", "/api/auth/me", None),
        ],
    )
    def test_no_token_rejected_on_mutating_routes(self, client, method, path, json_body):
        resp = getattr(client, method)(path, json=json_body) if json_body is not None else getattr(client, method)(path)
        assert resp.status_code == 401


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

    def test_ownership_enforced(self, client, owner, other_user, priced_instrument):
        create = client.post("/api/watchlists", json={"name": "Private"}, headers=_auth(owner))
        wl_id = create.json()["id"]
        add = client.post(
            f"/api/watchlists/{wl_id}/items", json={"instrument_id": priced_instrument.id}, headers=_auth(owner)
        )
        assert add.status_code == 201

        other_headers = _auth(other_user)
        listed = client.get("/api/watchlists", headers=other_headers)
        assert all(w["id"] != wl_id for w in listed.json()["items"])

        assert client.get(f"/api/watchlists/{wl_id}/view", headers=other_headers).status_code == 404
        assert client.patch(f"/api/watchlists/{wl_id}", json={"name": "Hijacked"}, headers=other_headers).status_code == 404
        # Adding/removing items on someone else's watchlist by guessing its
        # id -- must 404 exactly like every other access to it, not succeed
        # silently or leak a different status that confirms the id exists.
        hijack_add = client.post(
            f"/api/watchlists/{wl_id}/items", json={"instrument_id": priced_instrument.id}, headers=other_headers
        )
        assert hijack_add.status_code == 404
        hijack_remove = client.delete(
            f"/api/watchlists/{wl_id}/items/{priced_instrument.id}", headers=other_headers
        )
        assert hijack_remove.status_code == 404
        assert client.delete(f"/api/watchlists/{wl_id}", headers=other_headers).status_code == 404

        # The item added by the real owner must still be there -- proves the
        # rejected hijack_remove call above didn't silently succeed anyway.
        view = client.get(f"/api/watchlists/{wl_id}/view", headers=_auth(owner))
        assert len(view.json()["items"]) == 1


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
        listed = client.get("/api/screens", headers=other_headers)
        assert listed.status_code == 200
        assert all(s["id"] != screen_id for s in listed.json()["items"])

        assert client.patch(f"/api/screens/{screen_id}", json={"name": "Hijack"}, headers=other_headers).status_code == 404
        assert client.post(f"/api/screens/{screen_id}/run", headers=other_headers).status_code == 404
        assert client.delete(f"/api/screens/{screen_id}", headers=other_headers).status_code == 404

        # The rejected hijack calls above must not have mutated it -- the
        # real owner still sees their original screen, unchanged.
        still_listed = client.get("/api/screens", headers=_auth(owner))
        assert any(s["id"] == screen_id and s["name"] == "Private Screen" for s in still_listed.json()["items"])

    def test_from_text_daily_cap_blocks_after_limit_and_is_per_user(self, client, owner, other_user, monkeypatch):
        from app.api import screens as screens_module

        rule = {"type": "compare", "op": "gt", "field": "close", "value": 100}
        monkeypatch.setattr(screens_module, "translate_to_rule", lambda text: parse_screen_definition(rule))

        headers = _auth(owner)
        for _ in range(screens_module.nl_screen_daily_limiter.max_requests):
            resp = client.post("/api/screens/from-text", json={"text": "close above 100"}, headers=headers)
            assert resp.status_code == 200
        blocked = client.post("/api/screens/from-text", json={"text": "close above 100"}, headers=headers)
        assert blocked.status_code == 429
        assert "daily limit" in blocked.json()["detail"]

        # A different user has their own, untouched budget.
        other_resp = client.post("/api/screens/from-text", json={"text": "close above 100"}, headers=_auth(other_user))
        assert other_resp.status_code == 200


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

    def test_detail_returns_full_admin_view(self, client, owner, monkeypatch):
        from app.api import status as status_module

        monkeypatch.setattr(status_module, "nl_screen_status", lambda: (False, False))

        resp = client.get("/api/status/detail", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "latest_trade_date", "expected_trade_date", "is_current", "last_pipeline_run_at", "last_pipeline_status",
            "last_successful_pipeline_run_at", "instrument_count", "daily_price_count", "indicator_count",
            "recent_job_runs", "nl_screen_configured", "nl_screen_reachable",
        }
        assert body["nl_screen_configured"] is False
        assert body["nl_screen_reachable"] is False
        assert body["instrument_count"] >= 0
        assert len(body["recent_job_runs"]) <= 10

    def test_detail_last_successful_run_ignores_failed_runs(self, client, owner, db, monkeypatch):
        from app.api import status as status_module
        from app.jobs.daily_pipeline import PIPELINE_JOB_NAME

        monkeypatch.setattr(status_module, "nl_screen_status", lambda: (False, False))

        older_success = datetime(2099, 1, 1, tzinfo=timezone.utc)
        newer_failure = datetime(2099, 1, 2, tzinfo=timezone.utc)
        db.add(JobRun(job_name=PIPELINE_JOB_NAME, run_date=older_success.date(), status="success",
                       started_at=older_success, finished_at=older_success))
        db.add(JobRun(job_name=PIPELINE_JOB_NAME, run_date=newer_failure.date(), status="failed",
                       started_at=newer_failure, finished_at=newer_failure))
        db.flush()

        resp = client.get("/api/status/detail", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_pipeline_status"] == "failed"  # most recent run, regardless of outcome
        assert body["last_successful_pipeline_run_at"].startswith("2099-01-01")


class TestUnhandledErrors:
    def test_no_traceback_or_internal_detail_leaked_to_client(self, client, owner, monkeypatch):
        """FastAPI/Starlette's default behavior for an exception with no
        registered handler: log it server-side, return a generic 500 with no
        traceback. This has never been explicitly configured (no debug=True,
        no custom exception handler) -- confirming that's actually true,
        rather than assuming the framework default holds, is the point.
        TestClient normally re-raises server exceptions into the test
        process instead of returning the HTTP response a real client would
        see -- raise_server_exceptions=False turns that off so this test
        observes what an actual caller gets."""
        from app.api import status as status_module

        def boom(*a, **k):
            raise RuntimeError("postgresql://stock:secret-password@db-host/stock -- /app/app/api/status.py line 20")

        monkeypatch.setattr(status_module, "most_recent_trading_day", boom)

        raw_client = TestClient(app, raise_server_exceptions=False)
        resp = raw_client.get("/api/status", headers=_auth(owner))

        assert resp.status_code == 500
        body_text = resp.text
        assert "secret-password" not in body_text
        assert "status.py" not in body_text
        assert "Traceback" not in body_text
        assert "RuntimeError" not in body_text


class TestSecurityHeaders:
    def test_baseline_headers_present(self, client, owner):
        resp = client.get("/api/status", headers=_auth(owner))
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "content-security-policy" in resp.headers

    def test_hsts_only_set_in_production(self, client, owner, monkeypatch):
        from app import security_headers

        monkeypatch.setattr(security_headers.settings, "app_env", "development")
        assert "strict-transport-security" not in client.get("/api/status", headers=_auth(owner)).headers

        monkeypatch.setattr(security_headers.settings, "app_env", "production")
        assert "strict-transport-security" in client.get("/api/status", headers=_auth(owner)).headers


class TestHealth:
    def test_ok_when_db_reachable(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_failure_does_not_leak_internal_detail(self, client, monkeypatch):
        from app.api import health as health_module

        class _BoomEngine:
            def connect(self):
                raise RuntimeError("postgresql://stock:secret-password@db-host:5432/stock is unreachable")

        monkeypatch.setattr(health_module, "engine", _BoomEngine())

        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "unhealthy"}


class TestInstrumentCrossover:
    def test_returns_series_for_valid_periods(self, client, db, owner):
        from app.models import DailyPrice, Instrument
        from datetime import date, timedelta

        inst = Instrument(symbol="XOVR", exchange="NSE", company_name="Crossover Co", is_active=True)
        db.add(inst)
        db.flush()
        start = date(2026, 1, 1)
        for i, close in enumerate([10, 10, 10, 10, 10, 10, 30, 30, 30]):
            db.add(DailyPrice(
                instrument_id=inst.id, trade_date=start + timedelta(days=i),
                open=close, high=close, low=close, close=close, adjusted_close=close, volume=100,
            ))
        db.flush()

        resp = client.get(
            f"/api/instruments/{inst.id}/crossover",
            params={"fast": 2, "slow": 3, "ma_type": "sma"},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["instrument_id"] == inst.id
        assert any(p["signal"] == "crossed_above" for p in body["points"])

    def test_rejects_fast_greater_than_slow(self, client, db, owner):
        from app.models import DailyPrice, Instrument
        from datetime import date

        inst = Instrument(symbol="BAD", exchange="NSE", company_name="Bad Co", is_active=True)
        db.add(inst)
        db.flush()
        db.add(DailyPrice(instrument_id=inst.id, trade_date=date(2026, 1, 1), open=1, high=1, low=1, close=1, adjusted_close=1, volume=1))
        db.flush()

        resp = client.get(
            f"/api/instruments/{inst.id}/crossover",
            params={"fast": 50, "slow": 20, "ma_type": "sma"},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_404_for_unknown_instrument(self, client, owner):
        resp = client.get(
            "/api/instruments/999999/crossover",
            params={"fast": 9, "slow": 21, "ma_type": "ema"},
            headers=_auth(owner),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/instruments/1/crossover", params={"fast": 9, "slow": 21, "ma_type": "ema"})
        assert resp.status_code == 401
