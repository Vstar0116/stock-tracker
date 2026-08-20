"""Slack-compatible webhook alerting for job failures.

A no-op unless ALERT_WEBHOOK_URL is set -- local dev is unaffected either
way. Chose a webhook over email: one URL, one HTTP POST via the httpx
dependency this app already has, no SMTP config surface (host/port/user/
password/TLS) for equivalent value. Any endpoint that accepts a Slack-shaped
{"text": "..."} payload works -- Slack incoming webhooks, Discord (via its
Slack-compatible webhook shim), Microsoft Teams (via a compatible
connector), or a custom receiver.

Delivery never raises into the caller -- a webhook being down must never be
the reason a job crashes or a healthcheck fails to record its own result.

Run `python -m app.services.alerting` to send a real test alert on demand.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

logger = logging.getLogger("alerting")

# Repeats of the SAME underlying problem within this window are suppressed --
# guards against an alert storm (e.g. a stale-pipeline healthcheck firing
# every hour with nothing new to say). 6 hours: long enough that a repeated
# healthcheck or a string of same-cause failures doesn't spam, short enough
# that a genuinely new day's failure (the next scheduled run, ~24h later)
# still always alerts.
DEDUPE_WINDOW = timedelta(hours=6)

# In-process only -- each scheduled job is a fresh CLI invocation (see
# deploy/systemd/), so this doesn't persist across runs. That's fine: the
# thing this must prevent is spamming once per retry attempt *within* one
# invocation (daily_pipeline.py retries the whole pipeline up to 3 times),
# not suppressing tomorrow's alert for today's problem -- callers already
# alert once per invocation outcome, not once per attempt, so cross-process
# dedup was never actually needed for that. This still helps for the
# healthcheck job, which re-invokes hourly while a problem is ongoing.
_last_sent: dict[str, datetime] = {}

# Matches scheme://user:pass@ in any string (e.g. a DATABASE_URL that ended
# up embedded in a driver's exception text) and blanks the credentials --
# applied to every outbound alert body as defense in depth, regardless of
# which code path produced the text. Confirmed necessary, not theoretical:
# a malformed DATABASE_URL raises sqlalchemy.exc.ArgumentError whose message
# echoes the raw connection string, credentials included.
_CREDENTIAL_PATTERN = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:]+:[^/\s@]+@")


def redact(text: str) -> str:
    return _CREDENTIAL_PATTERN.sub(r"\1***:***@", text)


def _deliver(title: str, detail: str) -> None:
    """Send unconditionally -- no config check, no dedup. Used by send_alert
    after its checks pass, and directly by the test-alert CLI below."""
    body = f"*{title}*\n{redact(detail)}"
    try:
        httpx.post(settings.alert_webhook_url, json={"text": body}, timeout=10)
    except httpx.HTTPError as exc:
        logger.error("alerting: failed to deliver %r: %s", title, exc)


def send_alert(title: str, detail: str, *, fingerprint: str | None = None) -> None:
    """Send a webhook alert. No-op if ALERT_WEBHOOK_URL isn't configured.

    `fingerprint` identifies "the same underlying problem" for dedup -- pass
    a stable string (e.g. "daily_pipeline:failed", not the full error text,
    which can vary run to run for the same root cause). Defaults to `title`.
    """
    if not settings.alert_webhook_url:
        return

    key = fingerprint or title
    now = datetime.now(timezone.utc)
    last = _last_sent.get(key)
    if last is not None and now - last < DEDUPE_WINDOW:
        logger.info("alerting: suppressing repeat of %r (last sent %s ago)", key, now - last)
        return

    _deliver(title, detail)
    _last_sent[key] = now


def main() -> None:
    if not settings.alert_webhook_url:
        print("ALERT_WEBHOOK_URL is not set -- alerting is disabled, nothing to test.")
        raise SystemExit(1)
    _deliver(
        "Test alert",
        f"This is a test alert from app_env={settings.app_env!r}, sent {datetime.now(timezone.utc).isoformat()}. "
        "If you're seeing this, alerting is wired up correctly.",
    )
    print("Test alert sent to the configured webhook.")


if __name__ == "__main__":
    main()
