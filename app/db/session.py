from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Pool settings matter here because this app is idle for most of the day and
# gets used after market close: by then every pooled connection has long
# outlived the managed database's idle timeout, and handing one out
# unchecked surfaces as "server closed the connection unexpectedly" on the
# first request of the evening.
#   pool_pre_ping -- one trivial round-trip per checkout to validate the
#     connection, transparently replacing it if it's dead.
#   pool_recycle  -- retire connections before the server's own idle timeout
#     rather than waiting to discover they're gone.
#   pool_size/max_overflow -- cap connections per worker so the (2, see
#     entrypoint.sh) gunicorn workers plus the scheduled jobs can't together
#     exhaust the database's connection limit.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
