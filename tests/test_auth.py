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

from app.api.auth import login_rate_limiter
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
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    login_rate_limiter.reset()
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
        for _ in range(login_rate_limiter.max_requests):
            client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        resp = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        assert resp.status_code == 429


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
