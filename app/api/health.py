from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.session import engine

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Liveness only -- no exception text (can include host/credentials)
        # goes to an unauthenticated caller. See /api/status for detail.
        raise HTTPException(status_code=503, detail="unhealthy") from exc
    return {"status": "ok"}
