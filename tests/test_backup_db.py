"""Unit coverage for the pure-logic pieces of app/jobs/backup_db.py.
The real end-to-end proof (pg_dump -> S3 -> pg_restore into a scratch DB,
data verified intact) is a manual, one-off exercise -- see DEPLOYMENT.md
"Backups & recovery" for that run's actual output; it isn't re-run on every
`pytest` invocation since it needs real pg_dump/pg_restore binaries and an
S3-compatible endpoint, neither of which belong in the regular test suite's
dependencies.
"""

import pytest

from app.jobs import backup_db


def test_pg_dump_dsn_strips_driver_suffix(monkeypatch):
    monkeypatch.setattr(backup_db.settings, "database_url", "postgresql+psycopg2://u:p@host:5432/db")
    assert backup_db._pg_dump_dsn() == "postgresql://u:p@host:5432/db"


def test_run_refuses_when_unconfigured(monkeypatch):
    monkeypatch.setattr(backup_db.settings, "backup_s3_bucket", None)
    with pytest.raises(RuntimeError, match="BACKUP_S3_BUCKET"):
        backup_db.run()
