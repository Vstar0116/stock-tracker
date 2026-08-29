"""API tests: happy path plus ownership enforcement, for every /api/* router.

Run with: pytest tests/test_api.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back, using a fresh
throwaway instrument so nothing here depends on (or mutates) real market data.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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


class TestCrossoverScan:
    def test_no_price_data_404s_via_global_domain_error_handler(self, client, owner, monkeypatch):
        """crossover_loader.run_scan raises NoPriceDataError (app/errors.py)
        when daily_prices is empty. This route used to catch that itself
        (`except ValueError as exc: raise HTTPException(404, ...)`); that
        local try/except is gone now -- app/main.py's global
        @app.exception_handler(DomainError) does the translation instead.
        This confirms the behavior is unchanged after removing it."""
        from app.api import crossover as crossover_module
        from app.errors import NoPriceDataError

        def boom(*a, **k):
            raise NoPriceDataError()

        # crossover.py does `from app.services.crossover_loader import
        # run_scan`, binding the name into ITS OWN module namespace -- must
        # patch it there, not on crossover_loader itself.
        monkeypatch.setattr(crossover_module, "run_scan", boom)

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 9, "slow": 21, "ma_type": "ema", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "no price data loaded yet"

    def test_finds_matches_across_instruments(self, client, db, owner, monkeypatch):
        import contextlib

        from sqlalchemy import text

        from app.models import DailyPrice, Instrument
        from app.services import crossover_loader

        # crossover_loader.run_scan opens its OWN db connection for the
        # cached, expensive part of the scan (see the module docstring in
        # app/services/crossover_loader.py) rather than using the `db`
        # session injected below -- so it can't see this test's
        # SAVEPOINT-backed, uncommitted rows unless pointed at the same
        # connection. Same pattern as tests/test_crossover_loader.py.
        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        # This dev DB now carries real, backfilled market data, and
        # resolve_window/load_wide are legitimately whole-market queries --
        # so seeded rows must land on real, already-existing trade dates
        # (anchored to the current as_of) rather than a hardcoded date range
        # that now falls outside the scan's window entirely. See
        # tests/test_crossover_loader.py's _recent_trade_dates for the same
        # pattern.
        dates = sorted(
            r[0]
            for r in db.execute(
                text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"),
                {"n": 9},
            ).fetchall()
        )

        crossing = Instrument(symbol="XOVR2", exchange="NSE", company_name="Crossing Co", sector="IT", is_active=True)
        flat = Instrument(symbol="FLAT2", exchange="NSE", company_name="Flat Co", is_active=True)
        db.add_all([crossing, flat])
        db.flush()

        # 8 flat bars then a jump on the real market's last 9 trading days --
        # fast(2) crosses above slow(3) on the final bar.
        for d, close in zip(dates, [10, 10, 10, 10, 10, 10, 10, 10, 30]):
            db.add(DailyPrice(instrument_id=crossing.id, trade_date=d, open=close, high=close, low=close, close=close, adjusted_close=close, volume=100))
        for d in dates:
            db.add(DailyPrice(instrument_id=flat.id, trade_date=d, open=50, high=50, low=50, close=50, adjusted_close=50, volume=100))
        db.flush()

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 3, "ma_type": "sma", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        symbols = {m["symbol"] for m in body["matches"]}
        assert "XOVR2" in symbols
        assert "FLAT2" not in symbols
        assert body["stats"]["matched"] == len(body["matches"])

    def test_stale_but_within_tolerance_match_has_null_latest_close(self, client, db, owner, monkeypatch):
        """Regression test: load_wide (crossover_loader) forward-fills short
        gaps within STALE_TOLERANCE_DAYS, so an instrument with no price row
        on the exact as_of date can still be a genuine match. The API's
        hydration query used to INNER JOIN on DailyPrice for
        trade_date == as_of, silently dropping exactly these matches after
        the scan had already found them. It must now show up with
        latest_close: null instead of vanishing."""
        import contextlib

        from sqlalchemy import text

        from app.models import DailyPrice, Instrument
        from app.services import crossover_loader

        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        dates = sorted(
            r[0]
            for r in db.execute(
                text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"),
                {"n": 10},
            ).fetchall()
        )

        stale = Instrument(symbol="STALEX", exchange="NSE", company_name="Stale Co", is_active=True)
        db.add(stale)
        db.flush()

        # 8 real bars ending 2 trading days before as_of -- fast(2)/slow(5)
        # SMA crosses above exactly on what becomes the scan's last bar once
        # forward-filled. The last 2 real dates are deliberately left
        # unseeded for this instrument to simulate a trading halt / sparse
        # bhavcopy within STALE_TOLERANCE_DAYS (5 trading days).
        closes = [46, 50, 23, 34, 49, 25, 36, 35]
        for d, close in zip(dates[:8], closes):
            db.add(DailyPrice(instrument_id=stale.id, trade_date=d, open=close, high=close, low=close, close=close, adjusted_close=close, volume=100))
        db.flush()

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 5, "ma_type": "sma", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        match = next((m for m in body["matches"] if m["symbol"] == "STALEX"), None)
        assert match is not None
        assert match["signal"] == "crossed_above"
        assert match["latest_close"] is None

    def test_rejects_invalid_periods(self, client, owner):
        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 50, "slow": 20, "ma_type": "sma", "direction": "any"},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.post("/api/scans/crossover", json={"fast": 9, "slow": 21, "ma_type": "ema", "direction": "any"})
        assert resp.status_code == 401


class TestZoneClassifier:
    # Note: the zone router requires auth (same `Depends(get_current_user)`
    # pattern as every other router -- see app/api/crossover.py and this
    # feature's design doc), so every request below needs `headers=_auth(owner)`.

    def test_get_zone_unknown_instrument_404s(self, client, owner):
        resp = client.get("/api/zone/999999", headers=_auth(owner))
        assert resp.status_code == 404

    def test_scan_with_no_price_data_404s_not_500(self, client, owner, monkeypatch):
        """Regression test: app/api/zone.py's scan route used to have NO
        try/except around run_zone_scan() at all -- unlike the analogous
        crossover scan route, which does catch this -- so on an empty
        database this raised a bare ValueError straight through to an
        unhandled 500. zone_loader.run_zone_scan now raises NoPriceDataError
        (app/errors.py) instead, and app/main.py's global
        @app.exception_handler(DomainError) catches it for every route that
        can raise it, without zone.py needing its own try/except at all."""
        from app.api import zone as zone_module
        from app.errors import NoPriceDataError

        def boom(*a, **k):
            raise NoPriceDataError()

        monkeypatch.setattr(zone_module, "run_zone_scan", boom)

        resp = client.get("/api/zone/scan", headers=_auth(owner))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "no price data loaded yet"

    def test_get_zone_invalid_params_422s(self, client, owner, instrument):
        resp = client.get(
            f"/api/zone/{instrument.id}",
            params={"fast_ema_period": 21, "slow_ema_period": 21},
            headers=_auth(owner),
        )
        assert resp.status_code == 422

    def test_get_zone_insufficient_history(self, client, db, owner, instrument, today):
        db.add(DailyPrice(
            instrument_id=instrument.id, trade_date=today,
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
            close=Decimal("100"), adjusted_close=Decimal("100"), volume=1000,
        ))
        db.flush()

        resp = client.get(f"/api/zone/{instrument.id}", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert body["zone"] == "Insufficient Data"
        assert body["rsi"] is None

    def test_get_zone_full_history_classifies(self, client, db, owner, instrument, today):
        for i in range(60):
            d = today - timedelta(days=59 - i)
            close = 100.0 + i * 0.5
            db.add(DailyPrice(
                instrument_id=instrument.id, trade_date=d,
                open=Decimal(str(close)), high=Decimal(str(close * 1.01)), low=Decimal(str(close * 0.99)),
                close=Decimal(str(close)), adjusted_close=Decimal(str(close)), volume=100000,
            ))
        db.flush()

        resp = client.get(
            f"/api/zone/{instrument.id}",
            params={"macro_sma_period": 20, "fast_ema_period": 5, "slow_ema_period": 10, "rsi_period": 14, "atr_period": 14, "rvol_period": 20},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["zone"] in ("A", "B", "C", "D", "Unclassified")
        assert body["ticker"] == instrument.symbol

    def test_scan_returns_params_and_evaluated_count(self, client, owner):
        resp = client.get("/api/zone/scan", headers=_auth(owner))
        assert resp.status_code == 200
        body = resp.json()
        assert "as_of" in body
        assert "matches" in body
        assert "skipped" in body
        assert body["evaluated"] >= 0

    def test_scan_matches_sorted_by_zone_then_rsi(self, client, owner):
        resp = client.get("/api/zone/scan", headers=_auth(owner))
        assert resp.status_code == 200
        matches = resp.json()["matches"]
        zone_order = {"A": 0, "B": 1, "C": 2, "D": 3, "Unclassified": 4}
        zones_seen = [zone_order[m["zone"]] for m in matches]
        assert zones_seen == sorted(zones_seen)

    def test_requires_auth(self, client):
        resp = client.get("/api/zone/scan")
        assert resp.status_code == 401


class TestPortfolioReports:
    """POST /api/portfolio-reports (+ list/get/delete/watchlist) and the
    ScanRequest.report_id / watchlist_only restriction on
    POST /api/scans/crossover. Uses the same checked-in real-world fixture
    as tests/test_portfolio_pdf.py (a 54-ticker BANYAN-STRATUM-V4 report)
    rather than a synthetic PDF, via TestClient's multipart `files=`.
    """

    FIXTURE = Path(__file__).parent / "fixtures" / "portfolio_report.pdf"

    def _upload(self, client, owner, filename="portfolio_report.pdf", content=None, content_type="application/pdf"):
        return client.post(
            "/api/portfolio-reports",
            files={"file": (filename, content or self.FIXTURE.read_bytes(), content_type)},
            headers=_auth(owner),
        )

    def test_upload_parses_and_persists_the_report(self, client, owner):
        resp = self._upload(client, owner)
        assert resp.status_code == 201
        body = resp.json()
        assert body["filename"] == "portfolio_report.pdf"
        assert body["report_date"] == "2026-08-22"
        assert body["ticker_count"] == 54
        # No matching instruments seeded in this test -- every row is
        # present but unmatched.
        assert body["matched_count"] == 0
        assert len(body["items"]) == 54
        trent = next(i for i in body["items"] if i["ticker"] == "TRENT")
        assert trent["matched"] is False
        assert trent["instrument_id"] is None
        assert trent["grp"] == "Core"
        assert trent["score"] == 4
        assert trent["zone"] == "A"

    def test_upload_matches_against_existing_instruments(self, client, db, owner):
        from app.models import Instrument

        inst = Instrument(symbol="TRENT", exchange="NSE", company_name="Trent Ltd", is_active=True)
        db.add(inst)
        db.flush()

        resp = self._upload(client, owner)
        assert resp.status_code == 201
        body = resp.json()
        assert body["matched_count"] == 1
        trent = next(i for i in body["items"] if i["ticker"] == "TRENT")
        assert trent["matched"] is True
        assert trent["instrument_id"] == inst.id
        assert trent["symbol"] == "TRENT"

    def test_rejects_non_pdf_content(self, client, owner):
        resp = self._upload(client, owner, filename="notes.txt", content=b"hello world", content_type="text/plain")
        assert resp.status_code == 415

    def test_rejects_unreadable_pdf_bytes(self, client, owner):
        resp = self._upload(client, owner, filename="fake.pdf", content=b"%PDF-1.4 not really a pdf")
        assert resp.status_code == 422

    def test_rejects_oversized_upload(self, client, owner, monkeypatch):
        from app.api import portfolio_reports

        monkeypatch.setattr(portfolio_reports, "MAX_UPLOAD_BYTES", 100)
        resp = self._upload(client, owner)  # real fixture is far bigger than 100 bytes
        assert resp.status_code == 413

    def test_requires_auth(self, client):
        resp = client.post("/api/portfolio-reports", files={"file": ("x.pdf", self.FIXTURE.read_bytes(), "application/pdf")})
        assert resp.status_code == 401

    def test_list_scoped_to_current_user(self, client, owner, other_user):
        assert self._upload(client, owner).status_code == 201
        resp = client.get("/api/portfolio-reports", headers=_auth(other_user))
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        resp = client.get("/api/portfolio-reports", headers=_auth(owner))
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_ownership_enforced_on_get_and_delete(self, client, owner, other_user):
        report_id = self._upload(client, owner).json()["id"]

        resp = client.get(f"/api/portfolio-reports/{report_id}", headers=_auth(other_user))
        assert resp.status_code == 404

        resp = client.delete(f"/api/portfolio-reports/{report_id}", headers=_auth(other_user))
        assert resp.status_code == 404

        resp = client.get(f"/api/portfolio-reports/{report_id}", headers=_auth(owner))
        assert resp.status_code == 200

        resp = client.delete(f"/api/portfolio-reports/{report_id}", headers=_auth(owner))
        assert resp.status_code == 204

    def test_save_as_watchlist_creates_a_watchlist_of_matched_instruments(self, client, db, owner):
        from app.models import Instrument

        trent = Instrument(symbol="TRENT", exchange="NSE", company_name="Trent Ltd", is_active=True)
        db.add(trent)
        db.flush()

        report_id = self._upload(client, owner).json()["id"]

        resp = client.post(f"/api/portfolio-reports/{report_id}/watchlist", headers=_auth(owner))
        assert resp.status_code == 201
        watchlist_id = resp.json()["id"]

        view = client.get(f"/api/watchlists/{watchlist_id}/view", headers=_auth(owner))
        assert view.status_code == 200
        symbols = {row["symbol"] for row in view.json()["items"]}
        assert symbols == {"TRENT"}

    def test_save_as_watchlist_with_no_matches_is_rejected(self, client, owner):
        report_id = self._upload(client, owner).json()["id"]  # no instruments seeded -- nothing matches
        resp = client.post(f"/api/portfolio-reports/{report_id}/watchlist", headers=_auth(owner))
        assert resp.status_code == 422

    def test_scan_report_id_restricts_matches_and_carries_pdf_fields(self, client, db, owner, monkeypatch):
        import contextlib

        from sqlalchemy import text

        from app.models import DailyPrice, Instrument
        from app.services import crossover_loader

        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        dates = sorted(
            r[0]
            for r in db.execute(
                text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"), {"n": 9}
            ).fetchall()
        )

        # TRENT is in the PDF; OTHR2 is not -- both cross the same way, so
        # only the report_id filter (not the crossover logic) explains OTHR2
        # being excluded below.
        trent = Instrument(symbol="TRENT", exchange="NSE", company_name="Trent Ltd", sector="Retail", is_active=True)
        other = Instrument(symbol="OTHR2", exchange="NSE", company_name="Other Co", is_active=True)
        db.add_all([trent, other])
        db.flush()
        for inst in (trent, other):
            for d, close in zip(dates, [10, 10, 10, 10, 10, 10, 10, 10, 30]):
                db.add(
                    DailyPrice(
                        instrument_id=inst.id, trade_date=d, open=close, high=close, low=close,
                        close=close, adjusted_close=close, volume=100,
                    )
                )
        db.flush()

        report_id = self._upload(client, owner).json()["id"]

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 3, "ma_type": "sma", "direction": "any", "report_id": report_id},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        body = resp.json()
        symbols = {m["symbol"] for m in body["matches"]}
        assert symbols == {"TRENT"}
        assert body["stats"]["universe"] == 1

        trent_match = next(m for m in body["matches"] if m["symbol"] == "TRENT")
        assert trent_match["pdf_group"] == "Core"
        assert trent_match["pdf_score"] == 4
        assert trent_match["pdf_zone"] == "A"
        assert trent_match["pdf_price"] == 2924.00

    def test_scan_report_id_for_someone_elses_report_404s(self, client, owner, other_user):
        report_id = self._upload(client, owner).json()["id"]
        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 3, "ma_type": "sma", "direction": "any", "report_id": report_id},
            headers=_auth(other_user),
        )
        assert resp.status_code == 404

    def test_scan_watchlist_only_restricts_to_watchlisted_instruments(self, client, db, owner, monkeypatch):
        import contextlib

        from sqlalchemy import text

        from app.models import DailyPrice, Instrument, Watchlist, WatchlistItem
        from app.services import crossover_loader

        monkeypatch.setattr(crossover_loader, "_connect", lambda: contextlib.nullcontext(db.connection()))
        crossover_loader._scan_cached.cache_clear()
        crossover_loader._load_wide_cached.cache_clear()

        dates = sorted(
            r[0]
            for r in db.execute(
                text("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT :n"), {"n": 9}
            ).fetchall()
        )

        listed = Instrument(symbol="WLST2", exchange="NSE", company_name="Listed Co", is_active=True)
        unlisted = Instrument(symbol="NOLST2", exchange="NSE", company_name="Unlisted Co", is_active=True)
        db.add_all([listed, unlisted])
        db.flush()
        for inst in (listed, unlisted):
            for d, close in zip(dates, [10, 10, 10, 10, 10, 10, 10, 10, 30]):
                db.add(
                    DailyPrice(
                        instrument_id=inst.id, trade_date=d, open=close, high=close, low=close,
                        close=close, adjusted_close=close, volume=100,
                    )
                )
        db.flush()

        watchlist = Watchlist(user_id=owner.id, name="my list")
        db.add(watchlist)
        db.flush()
        db.add(WatchlistItem(watchlist_id=watchlist.id, instrument_id=listed.id))
        db.flush()

        resp = client.post(
            "/api/scans/crossover",
            json={"fast": 2, "slow": 3, "ma_type": "sma", "direction": "any", "watchlist_only": True},
            headers=_auth(owner),
        )
        assert resp.status_code == 200
        symbols = {m["symbol"] for m in resp.json()["matches"]}
        assert "WLST2" in symbols
        assert "NOLST2" not in symbols
