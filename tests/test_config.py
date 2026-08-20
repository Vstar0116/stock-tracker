"""Tests for app/config.py's CORS origin parsing -- the one bit of config
logic here that isn't a plain field (see app/api/status.py's CORS comment /
DEPLOYMENT.md for why this must never be "*" in production).
"""

from app.config import Settings


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
