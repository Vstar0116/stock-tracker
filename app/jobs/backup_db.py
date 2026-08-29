"""Scheduled logical backup of the tables with no automated re-ingestion
source, to durable, off-host object storage.

Render's own Postgres backups (continuous PITR) are the first line of
defense and cover the whole database -- but they're retained only a few
days (Render account-plan dependent) and live entirely inside Render's
control plane: if that account is ever locked, deleted, or unreachable, so
is every backup it ever took. This job is the independent, off-platform
copy, and it's why DEPLOYMENT.md's restore runbook for it exists.

Table scope -- users, watchlists, watchlist_items, screens, alerts,
fundamentals -- is the set with no automated source (see DEPLOYMENT.md
"What's genuinely irreplaceable"). `instruments` is ALSO included even
though its content (symbol, company name, sector) is reconstructible from
NSE/BSE: it's a surrogate-key table, and watchlist_items/alerts/fundamentals
FK to those exact instrument_id values. A fresh re-ingest would assign new
IDs, not the ones the restored rows reference -- so instruments has to come
back from this backup too, or every foreign key in the other tables would
need manual remapping during restore. daily_prices/indicators/
corporate_actions are deliberately NOT here: reconstructible from source,
nothing else FKs to them, and including them would make this dump orders of
magnitude larger for no recovery benefit.

Run with: python -m app.jobs.backup_db
"""

import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

from app.config import settings
from app.db.session import SessionLocal
from app.jobs._tracking import track_job_run
from app.services import alerting
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("backup_db")

JOB_NAME = "backup_db"

# Dependency order (FK targets before the tables that reference them) --
# doesn't matter for the dump itself, but keeps the restore runbook in
# DEPLOYMENT.md readable, and --disable-triggers (used at restore time)
# means this order isn't load-bearing for correctness either way.
BACKUP_TABLES = ["instruments", "users", "watchlists", "watchlist_items", "screens", "alerts", "fundamentals"]


def _pg_dump_dsn() -> str:
    # pg_dump wants a plain postgresql:// URL; our SQLAlchemy DATABASE_URL
    # carries a "+psycopg2" driver suffix pg_dump doesn't understand.
    return settings.database_url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _dump(dest: Path) -> None:
    cmd = ["pg_dump", "--format=custom", "--file", str(dest)]
    for table in BACKUP_TABLES:
        cmd += ["--table", table]
    cmd.append(_pg_dump_dsn())

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {proc.stderr.strip()[:1000]}")


def _upload(local_path: Path, key: str) -> None:
    client = boto3.client(
        "s3",
        endpoint_url=settings.backup_s3_endpoint_url,  # None -> real AWS S3
        region_name=settings.backup_s3_region,
        aws_access_key_id=settings.backup_s3_access_key_id,
        aws_secret_access_key=settings.backup_s3_secret_access_key,
    )
    client.upload_file(str(local_path), settings.backup_s3_bucket, key)


def run() -> str:
    """Dump BACKUP_TABLES, upload to S3, record the run. Returns the S3 key."""
    if not settings.backup_s3_bucket:
        raise RuntimeError("BACKUP_S3_BUCKET is not set -- refusing to run with nowhere to put the backup")

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        with track_job_run(db, JOB_NAME, now.date()) as tracker:
            filename = f"stock-tracker-{now.strftime('%Y%m%dT%H%M%SZ')}.dump"
            key = f"{settings.backup_s3_prefix}/{filename}" if settings.backup_s3_prefix else filename
            with tempfile.TemporaryDirectory() as tmp:
                dump_path = Path(tmp) / filename
                _dump(dump_path)
                tracker.rows_processed = dump_path.stat().st_size
                _upload(dump_path, key)
        return key
    finally:
        db.close()


def main() -> int:
    try:
        key = run()
    except Exception as exc:
        logger.error("backup_db failed: %s", exc)
        alerting.send_alert(
            "backup_db failed",
            f"Time: {datetime.now(timezone.utc).isoformat()}\nError: {exc}\n\n"
            f"Check: python -m app.jobs.daily_pipeline status (job_runs has a 'backup_db' row)\n"
            f"See DEPLOYMENT.md 'Backups & recovery' for the restore runbook.",
            fingerprint="backup_db:failed",
        )
        return 1
    print(f"backup uploaded: s3://{settings.backup_s3_bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
