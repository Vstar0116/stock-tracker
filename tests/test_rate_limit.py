"""Tests for app.rate_limit.client_ip -- the X-Forwarded-For trust boundary.

No DB needed: client_ip is a pure function of the request object and
app.config.settings.

Run with: pytest tests/test_rate_limit.py -v
"""

from dataclasses import dataclass

import pytest

from app.config import settings
from app.rate_limit import client_ip


@dataclass
class _FakeClient:
    host: str


class _FakeRequest:
    def __init__(self, peer_ip: str, headers: dict[str, str] | None = None) -> None:
        self.client = _FakeClient(peer_ip)
        self.headers = headers or {}


class TestClientIp:
    def test_trusts_forwarded_header_from_trusted_proxy(self):
        req = _FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 127.0.0.1"})
        assert client_ip(req) == "203.0.113.7"

    def test_ignores_forwarded_header_from_untrusted_peer(self):
        # A request that reached the app directly (e.g. gunicorn bound to
        # 0.0.0.0 on a LAN, bypassing the reverse proxy) must be bucketed on
        # its real TCP peer IP, not a header it can set itself.
        req = _FakeRequest("192.168.1.50", {"x-forwarded-for": "1.2.3.4"})
        assert client_ip(req) == "192.168.1.50"

    def test_falls_back_to_peer_ip_when_no_forwarded_header(self):
        req = _FakeRequest("127.0.0.1")
        assert client_ip(req) == "127.0.0.1"

    def test_no_client_is_unknown(self):
        req = _FakeRequest("127.0.0.1")
        req.client = None
        assert client_ip(req) == "unknown"

    def test_respects_configured_trusted_proxy_set(self, monkeypatch):
        monkeypatch.setattr(type(settings), "trusted_proxy_ip_set", property(lambda self: {"10.0.0.1"}))
        req = _FakeRequest("10.0.0.1", {"x-forwarded-for": "9.9.9.9"})
        assert client_ip(req) == "9.9.9.9"
        untrusted = _FakeRequest("127.0.0.1", {"x-forwarded-for": "9.9.9.9"})
        assert client_ip(untrusted) == "127.0.0.1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
