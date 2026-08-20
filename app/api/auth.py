from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.rate_limit import RateLimiter
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security import DUMMY_PASSWORD_HASH, create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Both are checked on every login attempt. IP-based alone is trivially
# bypassed by an attacker who just rotates source IPs (cheap via any
# botnet/proxy pool) while hammering ONE victim's email -- the email-keyed
# limiter is what actually bounds that. IP-based alone would also mean a
# distributed attacker targeting many different accounts never trips any
# single account's limit. Same budget for both by default: 5 attempts/60s
# is generous for a real typo, tight for either brute-force shape.
login_ip_limiter = RateLimiter(key_prefix="login:ip", max_requests=5, window_seconds=60)
login_email_limiter = RateLimiter(key_prefix="login:email", max_requests=5, window_seconds=60)


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(login_ip_limiter)])
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    login_email_limiter.check(payload.email.strip().lower())

    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()

    # Always run bcrypt, even for an email that doesn't exist -- checking it
    # against a fixed dummy hash keeps a "no such user" response taking the
    # same ~100ms as a "wrong password" one, instead of returning near-
    # instantly and letting response time reveal which emails are registered.
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)

    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect email or password")

    token, expires_in = create_access_token(user.id)
    return LoginResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
