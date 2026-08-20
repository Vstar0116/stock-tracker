"""Minimal in-memory sliding-window rate limiter, used as a FastAPI dependency.

# ponytail: single-process, in-memory -- fine for one uvicorn worker serving
# 4-5 known users. If this app ever runs multiple workers/instances, swap for
# a shared store (Redis) so the limit is enforced across all of them.
"""

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] < cutoff:
                hits.pop(0)
            if len(hits) >= self.max_requests:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, try again later")
            hits.append(now)

    def reset(self) -> None:
        """Test hook -- clear all recorded hits."""
        with self._lock:
            self._hits.clear()
