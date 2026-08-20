"""Tests for email+password login, JWT access tokens, and the protected-route
dependency (app/api/deps.py::get_current_user).

Run with: pytest tests/test_auth.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import rate_limit
from app.api.auth import login_email_limiter, login_ip_limiter
from app.config import settings
from app.db.session import engine, get_db
from app.main import app
from app.models import User
from app.security import ALGORITHM, hash_password

EMAIL = "test.user@example.com"
PASSWORD = "correct-horse-battery"


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

    # The rate limiter opens its own SessionLocal() (it runs both as a
    # dependency and from inside the route body, neither of which is the
    # injected `db` above) -- point it at this test's own SAVEPOINT-backed
    # connection too, same pattern as app/jobs' tests, so login_attempts
    # rows never leak into the real shared dev database and are rolled back
    # with everything else when this test ends.
    monkeypatch.setattr(rate_limit, "SessionLocal", _session_factory)

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def user(db):
    u = User(email=EMAIL, name="Test User", password_hash=hash_password(PASSWORD), is_active=True)
    db.add(u)
    db.flush()
    return u


class TestLogin:
    def test_successful_login_returns_token(self, client, user):
        resp = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        payload = jwt.decode(body["access_token"], settings.jwt_secret_key, algorithms=[ALGORITHM])
        assert payload["sub"] == str(user.id)

    def test_wrong_password_rejected(self, client, user):
        resp = client.post("/api/auth/login", json={"email": EMAIL, "password": "not the password"})
        assert resp.status_code == 401

    def test_unknown_email_rejected(self, client, db):
        resp = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever"})
        assert resp.status_code == 401

    def test_rate_limit_blocks_after_too_many_attempts(self, client, user):
        for _ in range(login_ip_limiter.max_requests):
            client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        resp = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        assert resp.status_code == 429

    def test_email_limit_blocks_even_when_source_ip_rotates(self, client, user):
        """The real bypass an IP-only limiter has: an attacker spreads
        attempts across many source IPs to keep hammering ONE account.
        Simulated here via a distinct X-Forwarded-For per request -- each
        looks like a fresh IP to login_ip_limiter, so if only that limiter
        existed this loop would never 429. login_email_limiter (keyed on the
        target email, independent of source IP) is what actually catches it."""
        for i in range(login_email_limiter.max_requests):
            resp = client.post(
                "/api/auth/login",
                json={"email": EMAIL, "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )
            assert resp.status_code == 401
        resp = client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.99"},
        )
        assert resp.status_code == 429

    def test_rate_limit_is_per_email_not_global(self, client, user, db):
        """A blocked account must not lock out every other account -- confirms
        the email key, not just the existence of SOME limiter, is what's doing
        the blocking. Each request uses a distinct X-Forwarded-For so the
        (separately budgeted) IP limiter never factors into this at all --
        isolating the email limiter's behavior specifically."""
        other = User(email="second.user@example.com", name="Second", password_hash=user.password_hash, is_active=True)
        db.add(other)
        db.flush()

        for i in range(login_email_limiter.max_requests):
            client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers={"X-Forwarded-For": f"10.2.0.{i}"})
        blocked = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers={"X-Forwarded-For": "10.2.0.99"})
        assert blocked.status_code == 429

        # Different email, fresh IP, well under its own budget -- not blocked.
        resp = client.post(
            "/api/auth/login",
            json={"email": "second.user@example.com", "password": "wrong"},
            headers={"X-Forwarded-For": "10.2.0.200"},
        )
        assert resp.status_code == 401  # wrong password, but NOT 429


class TestProtectedRoute:
    def test_no_token_rejected(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_valid_token_allows_access(self, client, user):
        login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = login.json()["access_token"]
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == EMAIL

    def test_expired_token_rejected(self, client, user):
        expired_payload = {"sub": str(user.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}
        token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=ALGORITHM)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
