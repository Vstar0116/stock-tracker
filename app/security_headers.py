"""Baseline hardening headers, absent from a plain FastAPI app by default.

This app only ever returns JSON (or, in dev, the interactive docs UI --
disabled in production, see app/main.py) -- nothing here is meant to be
rendered as a page, so `default-src 'none'` and the frame/sniff protections
below have no functional downside.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        if settings.app_env.lower() == "production":
            # Meaningful only over HTTPS, which is how this is actually
            # served in production (see DEPLOYMENT.md) -- not sent in local
            # dev (plain http) to avoid implying a guarantee that isn't true
            # there. Tells the browser to never downgrade to http for this
            # origin again, even if something upstream ever misconfigures a
            # redirect -- defense in depth, not the primary HTTPS enforcement
            # (that's Render's edge; see DEPLOYMENT.md "HTTPS").
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
