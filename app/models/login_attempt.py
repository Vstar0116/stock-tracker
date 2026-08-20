from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginAttempt(Base):
    """One row per login attempt, keyed by "ip:<addr>" or "email:<address>" --
    see app/rate_limit.py. Rows outside the limiter's own window are deleted
    as part of every check, so this table stays bounded by (active keys x
    max_requests), not by total traffic over time."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(320), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("ix_login_attempts_key_attempted_at", LoginAttempt.key, LoginAttempt.attempted_at)
