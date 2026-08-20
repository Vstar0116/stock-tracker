"""Password hashing (bcrypt) and JWT access tokens for email+password login."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# A real bcrypt hash with no matching password -- checked against on a
# "no such user" login instead of skipping verify_password entirely, so that
# path costs the same ~100ms as a wrong-password login and doesn't let an
# attacker distinguish "unregistered email" from "wrong password" by timing.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-timing-decoy")


def create_access_token(user_id: int) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    expires_in = settings.jwt_expire_minutes * 60
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    token = jwt.encode({"sub": str(user_id), "exp": expire}, settings.jwt_secret_key, algorithm=ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> int:
    """Returns the user id encoded in a valid, unexpired token. Raises
    jwt.PyJWTError (expired, bad signature, malformed, ...) otherwise."""
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    return int(payload["sub"])
