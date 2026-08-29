"""Domain exception hierarchy for the API layer.

Before this, each service raised its own unrelated exception (a bare
ValueError for "no price data yet", a module-local PortfolioPdfError,
NlScreenError, ...) and every route that could hit one hand-translated it to
an HTTPException with its own try/except -- ~6-7 near-identical blocks spread
across app/api/*.py, easy to duplicate and just as easy to forget (see
app/api/zone.py's scan route, which forgot one: it never caught the "no price
data" case at all, so an empty database 500s there instead of 404ing like the
analogous crossover scan does).

Raising one of these instead lets app/main.py's single
`@app.exception_handler(DomainError)` do that translation everywhere at once.
This is deliberately NOT meant to replace Pydantic's own ValueError-based
validator contract (schemas/*.py's @field_validator/@model_validator, or
anything constructed inside FastAPI's own Query/Body validation) -- those
must keep raising plain ValueError/AssertionError, since that's what
Pydantic's machinery itself expects. This hierarchy is for runtime,
post-validation domain failures: something the request shape was fine but the
data/state doesn't support.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every domain-level failure that should become a clean JSON
    error response instead of an unhandled 500. Subclasses set status_code;
    the message is whatever's safe to show the caller (never a raw
    exception/SQL string -- callers of these are trusted to keep it that way,
    the same discipline already used for every hand-written HTTPException
    detail in this codebase)."""

    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class UnprocessableError(DomainError):
    status_code = 422


class NoPriceDataError(NotFoundError):
    """Raised by crossover_loader.resolve_window / zone_loader.run_zone_scan
    when daily_prices is empty -- the nightly pipeline hasn't run yet, or
    this is a fresh environment with no data loaded."""

    def __init__(self, message: str = "no price data loaded yet") -> None:
        super().__init__(message)
