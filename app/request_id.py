"""Per-request correlation id.

Generates (or reuses a caller-supplied) X-Request-ID, binds it for the
duration of the request so every log line emitted while handling it -- from
the route handler down through any service call -- carries the same id (see
app/logging_config.py's contextvar), and echoes it back as a response
header so a client (or a support ticket quoting it) can be matched back to
the server-side logs for that exact request.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import set_request_id

_HEADER = "X-Request-ID"
# Accepting a caller-supplied id is safe -- it's never used for anything but
# log correlation, so a client can't affect another client's request by
# forging one. Still validated (not just length-capped) before it's echoed
# into a response header or a JSON log line: an untrusted value with
# newlines/control characters has no business in either.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        supplied = request.headers.get(_HEADER)
        request_id = supplied if supplied and _VALID_ID.match(supplied) else uuid.uuid4().hex
        set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)
        response.headers[_HEADER] = request_id
        return response
