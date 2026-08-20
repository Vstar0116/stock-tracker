"""Postgres-backed sliding-window rate limiter, used both as a FastAPI
dependency (per-request-IP) and called directly (per-account, since the
value being limited on -- email -- only exists after the request body is
parsed, not at the Request level a dependency sees).

Backed by the database, not memory: the web service runs WEB_CONCURRENCY
gunicorn workers (see entrypoint.sh) as separate OS processes with no
shared memory, so an in-memory counter would let each worker enforce its
own separate budget -- an attacker gets roughly max_requests * worker_count
through, not max_requests, purely from which worker a connection happens to
land on. A shared table closes that regardless of worker count.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.models import LoginAttempt


def client_ip(request: Request) -> str:
    """The real client IP behind Render's reverse proxy. X-Forwarded-For is
    trusted here ONLY because this app is never reachable except through
    Render's edge (see DEPLOYMENT.md) -- request.client.host alone would be
    Render's proxy IP for every request, bucketing all external traffic
    (attacker and every legitimate user alike) under one shared key. If this
    ever runs behind a different or additional proxy layer, this needs a
    trusted-hop-count / allowlist check instead of trusting the header
    outright."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, key_prefix: str, max_requests: int, window_seconds: float) -> None:
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)

    def check(self, key: str) -> None:
        """Raises 429 if `key` is already at its budget for the current
        window; otherwise records this attempt. Every call also deletes this
        key's rows outside the window, so the table stays bounded by
        (active keys x max_requests) rather than growing with total traffic."""
        full_key = f"{self.key_prefix}:{key}"
        now = datetime.now(timezone.utc)
        cutoff = now - self.window
        db = SessionLocal()
        try:
            db.execute(delete(LoginAttempt).where(LoginAttempt.key == full_key, LoginAttempt.attempted_at < cutoff))
            count = db.execute(
                select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key == full_key)
            ).scalar_one()
            if count >= self.max_requests:
                db.commit()  # keep the cleanup even though this attempt is being rejected
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, try again later")
            db.add(LoginAttempt(key=full_key, attempted_at=now))
            db.commit()
        finally:
            db.close()

    def __call__(self, request: Request) -> None:
        """FastAPI dependency form -- limits by apparent client IP."""
        self.check(client_ip(request))
