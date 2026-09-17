"""Tests for app/config.py's CORS origin parsing -- the one bit of config
logic here that isn't a plain field (see app/api/status.py's CORS comment /
DEPLOYMENT.md for why this must never be "*" in production) -- and the
production-database guard (see DEPLOYMENT.md "Known gaps" -- a real
single-host deployment legitimately runs Postgres on localhost, so this
must only reject the literal dev-default connection string, not any
localhost DATABASE_URL).
"""

import pytest
from pydantic import ValidationError

from app.config import DEV_DEFAULT_DATABASE_URL, Settings


def test_rejects_dev_default_database_in_production(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", DEV_DEFAULT_DATABASE_URL)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_allows_a_different_localhost_database_in_production(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://stock:a-real-strong-password@localhost:5432/stock")
    s = Settings(_env_file=None)
    assert s.app_env == "production"


def test_dev_default_database_is_fine_outside_production(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", DEV_DEFAULT_DATABASE_URL)
    s = Settings(_env_file=None)
    assert s.database_url == DEV_DEFAULT_DATABASE_URL


def test_cors_origin_list_splits_and_strips(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example.com ,https://b.example.com")
    s = Settings(_env_file=None)
    assert s.cors_origin_list == ["https://a.example.com", "https://b.example.com"]


def test_cors_origin_list_defaults_to_local_dev(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    s = Settings(_env_file=None)
    assert s.cors_origin_list == ["http://localhost:5173", "http://127.0.0.1:5173"]
