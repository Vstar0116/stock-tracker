"""Tests for app/jobs/healthcheck.py -- the independent "has the pipeline
gone stale" watcher. alerting.send_alert is monkeypatched throughout; no
real webhook calls.

Run with: pytest tests/test_healthcheck.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs import healthcheck
from app.jobs.daily_pipeline import PIPELINE_JOB_NAME
from app.models import JobRun


@pytest.fixture()
def db(monkeypatch):
    connection = engine.connect()
    trans = connection.begin()

    def _session_factory():
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(healthcheck, "SessionLocal", _session_factory)
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture()
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(healthcheck.alerting, "send_alert", lambda title, detail, **k: sent.append((title, k.get("fingerprint"))))
    return sent


# Far-future anchor for "now" -- the dev DB the test suite runs against
# already has real daily_pipeline job_runs rows from actual runs (this is a
# shared, not per-test-suite, database). Any real 2026 row is thousands of
# hours older than this, so it can never accidentally win MAX(started_at)
# over a row this test seeds itself, and can never accidentally look
# "recent" relative to it either -- the test is deterministic regardless of
# whatever real data already exists.
FAR_FUTURE_NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _job_run(db, started_at, status="success"):
    db.add(JobRun(job_name=PIPELINE_JOB_NAME, run_date=started_at.date(), status=status, started_at=started_at, finished_at=started_at))
    db.flush()


class TestNeverRun:
    def test_no_job_runs_row_at_all_alerts(self, db, alerts):
        # No row seeded relative to FAR_FUTURE_NOW -- whatever real rows
        # exist are so far in the past relative to it that this behaves
        # identically to "never run" from the check's perspective.
        healthcheck.run(now=FAR_FUTURE_NOW)
        assert len(alerts) == 1
        assert alerts[0][1] == "daily_pipeline:stale"


class TestRecentRun:
    def test_run_within_36h_does_not_alert(self, db, alerts):
        _job_run(db, FAR_FUTURE_NOW - timedelta(hours=20))
        healthcheck.run(now=FAR_FUTURE_NOW)
        assert alerts == []


class TestStaleRun:
    def test_run_older_than_36h_alerts(self, db, alerts):
        _job_run(db, FAR_FUTURE_NOW - timedelta(hours=40))
        healthcheck.run(now=FAR_FUTURE_NOW)
        assert len(alerts) == 1
        assert alerts[0][1] == "daily_pipeline:stale"

    def test_uses_the_most_recent_run_not_the_oldest(self, db, alerts):
        _job_run(db, FAR_FUTURE_NOW - timedelta(hours=100))
        _job_run(db, FAR_FUTURE_NOW - timedelta(hours=10))
        healthcheck.run(now=FAR_FUTURE_NOW)
        assert alerts == []  # the recent one is what matters


class TestDbConnectionFailure:
    def test_query_failure_alerts_with_db_connection_fingerprint(self, db, alerts, monkeypatch):
        from sqlalchemy.exc import OperationalError

        def boom(*a, **k):
            raise OperationalError("SELECT ...", {}, Exception("connection refused"))

        monkeypatch.setattr(Session, "execute", boom)

        healthcheck.run(now=FAR_FUTURE_NOW)

        assert len(alerts) == 1
        assert alerts[0][1] == "healthcheck:db_connection_failed"
