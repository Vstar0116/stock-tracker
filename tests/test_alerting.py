"""Tests for app/services/alerting.py -- no real network calls; httpx.post is
monkeypatched throughout.

Run with: pytest tests/test_alerting.py -v
"""

import httpx
import pytest

from app.services import alerting


@pytest.fixture(autouse=True)
def _reset_dedupe_cache():
    """Every test starts with a clean dedupe cache -- it's module-level
    process state, so tests would otherwise bleed into each other."""
    alerting._last_sent.clear()
    yield
    alerting._last_sent.clear()


@pytest.fixture()
def webhook_url(monkeypatch):
    url = "https://hooks.example.com/services/T00/B00/xxx"
    monkeypatch.setattr(alerting.settings, "alert_webhook_url", url)
    return url


class TestNoOpWhenUnconfigured:
    def test_send_alert_makes_no_http_call_when_webhook_unset(self, monkeypatch):
        monkeypatch.setattr(alerting.settings, "alert_webhook_url", None)
        calls = []
        monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)))

        alerting.send_alert("title", "detail")

        assert calls == []


class TestDelivery:
    def test_send_alert_posts_title_and_detail_to_webhook(self, webhook_url, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append((url, json, timeout)))

        alerting.send_alert("Something broke", "the details")

        assert len(calls) == 1
        url, payload, timeout = calls[0]
        assert url == webhook_url
        assert "Something broke" in payload["text"]
        assert "the details" in payload["text"]
        assert timeout == 10

    def test_delivery_failure_is_swallowed_not_raised(self, webhook_url, monkeypatch):
        def boom(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "post", boom)

        alerting.send_alert("title", "detail")  # must not raise


class TestDedupe:
    def test_same_fingerprint_within_window_sends_once(self, webhook_url, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

        alerting.send_alert("title", "detail 1", fingerprint="same-thing")
        alerting.send_alert("title", "detail 2 (different text, same problem)", fingerprint="same-thing")

        assert len(calls) == 1

    def test_different_fingerprint_sends_independently(self, webhook_url, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

        alerting.send_alert("title", "detail", fingerprint="problem-a")
        alerting.send_alert("title", "detail", fingerprint="problem-b")

        assert len(calls) == 2

    def test_default_fingerprint_is_the_title(self, webhook_url, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

        alerting.send_alert("Repeated Title", "detail 1")
        alerting.send_alert("Repeated Title", "detail 2")
        alerting.send_alert("Different Title", "detail 3")

        assert len(calls) == 2


class TestRedaction:
    def test_redacts_credentials_from_url_shaped_text(self):
        text = "connect failed: postgresql+psycopg2://stock:hunter2@localhost:5432/stock"
        redacted = alerting.redact(text)
        assert "hunter2" not in redacted
        assert "stock:hunter2" not in redacted
        assert "postgresql+psycopg2://***:***@localhost:5432/stock" in redacted

    def test_leaves_plain_text_untouched(self):
        text = "connection to server at \"localhost\", port 5432 failed: Connection refused"
        assert alerting.redact(text) == text

    def test_send_alert_redacts_detail_before_delivery(self, webhook_url, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

        alerting.send_alert("DB error", "url was postgresql://user:supersecret@host/db")

        assert "supersecret" not in calls[0]["text"]
