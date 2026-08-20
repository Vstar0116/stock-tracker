"""Tests for the daily pipeline orchestrator (app/jobs/daily_pipeline.py) --
stop-on-first-failure and dry-run-writes-nothing are the two properties that
matter here. Each step's own logic (ingest, adjust, compute, screen) is
exercised by its own test file; this only tests the sequencing, using fake
steps so no real job runs and no network is touched.

Run with: pytest tests/test_daily_pipeline.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs import daily_pipeline
from app.models import JobRun


@pytest.fixture()
def db(monkeypatch):
    connection = engine.connect()
    trans = connection.begin()

    def _session_factory():
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(daily_pipeline, "SessionLocal", _session_factory)
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


class TestStopsOnFirstFailure:
    def test_later_steps_never_run_and_failure_is_recorded(self, db, monkeypatch):
        calls = []

        def ok(dry_run):
            calls.append("a")
            return "ok"

        def fails(dry_run):
            calls.append("b")
            raise RuntimeError("boom")

        def never(dry_run):
            calls.append("c")
            return "ok"

        monkeypatch.setattr(daily_pipeline, "STEPS", [("a", ok), ("b", fails), ("c", never)])

        with pytest.raises(RuntimeError, match="boom"):
            daily_pipeline.run_pipeline(dry_run=False)

        assert calls == ["a", "b"]  # "c" is never called once "b" raises

        # order_by id desc, not scalar_one() -- the dev DB may already have
        # real daily_pipeline rows from actual runs; we only care about ours.
        row = (
            db.execute(select(JobRun).where(JobRun.job_name == daily_pipeline.PIPELINE_JOB_NAME).order_by(JobRun.id.desc()))
            .scalars()
            .first()
        )
        assert row is not None
        assert row.status == "failed"
        assert "'b'" in row.error_message


class TestDryRun:
    def test_dry_run_passes_flag_through_and_writes_no_job_run(self, db, monkeypatch):
        seen = []

        def step(dry_run):
            seen.append(dry_run)
            return "would do nothing"

        monkeypatch.setattr(daily_pipeline, "STEPS", [("a", step), ("b", step)])

        count_query = select(func.count()).select_from(JobRun).where(JobRun.job_name == daily_pipeline.PIPELINE_JOB_NAME)
        before = db.execute(count_query).scalar_one()

        daily_pipeline.run_pipeline(dry_run=True)

        assert seen == [True, True]
        after = db.execute(count_query).scalar_one()
        assert after == before  # dry run wrote no new job_runs row


class TestSuccess:
    def test_all_steps_run_records_success(self, db, monkeypatch):
        monkeypatch.setattr(daily_pipeline, "STEPS", [("a", lambda dry_run: "ok"), ("b", lambda dry_run: "ok")])

        daily_pipeline.run_pipeline(dry_run=False)

        row = (
            db.execute(select(JobRun).where(JobRun.job_name == daily_pipeline.PIPELINE_JOB_NAME).order_by(JobRun.id.desc()))
            .scalars()
            .first()
        )
        assert row is not None
        assert row.status == "success"
        assert row.rows_processed == 2
