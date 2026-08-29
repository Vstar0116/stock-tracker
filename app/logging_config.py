"""Structured (JSON) logging, shared by the API process and every
app/jobs/*.py script.

Before this, each job module called its own `logging.basicConfig(...)`
independently -- 9 identical copies. That "worked" only by luck:
`logging.basicConfig` is a no-op once the root logger already has a handler,
so whichever module happened to be imported (and therefore call
`basicConfig`) first in a given process silently won, and every copy just
happened to configure the same thing. One shared `configure_logging()`
replaces all of them.

Every log record also carries the current request's correlation id (set by
RequestIDMiddleware in app/main.py) via a contextvar + logging.Filter, so a
log line from deep inside a service call can be tied back to the HTTP
request that triggered it without threading a request_id parameter through
every function call in between. Outside a request (a cron job, a script run
from a shell) the field is simply omitted from the line.
"""

from __future__ import annotations

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import datetime, timezone

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal hand-rolled JSON-lines formatter -- deliberately not a new
    dependency (e.g. structlog/python-json-logger) for what's ~15 lines of
    stdlib logging; this codebase's own stated bias is toward minimal
    dependencies (see CLAUDE.md's "keep it simple, no Airflow at this
    scale"). One JSON object per line, the conventional shape for a log
    aggregator to ingest line-by-line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent -- safe to call from every job module's top level and from
    app/main.py without double-registering handlers (daily_pipeline.py, for
    instance, itself imports several other job modules at module level)."""
    global _configured
    if _configured:
        return
    _configured = True

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": _RequestIdFilter}},
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "root": {"level": level, "handlers": ["stdout"]},
        }
    )
