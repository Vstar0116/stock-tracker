"""Tests for the daily pipeline orchestrator (app/jobs/daily_pipeline.py) --
stop-on-first-failure and dry-run-writes-nothing are the two properties that
matter here. Each step's own logic (ingest, adjust, compute, screen) is
exercised by its own test file; this only tests the sequencing, using fake
steps so no real job runs and no network is touched.

Run with: pytest tests/test_daily_pipeline.py -v
Requires the local Postgres (docker compose up -d) -- runs inside a
SAVEPOINT-backed transaction that's always rolled back.
"""

from datetime import date
from datetime import datetime as real_datetime
from datetime import timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs import daily_pipeline
from app.models import CorporateAction, DailyPrice, Instrument, JobRun


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


@pytest.fixture()
def alerts(monkeypatch):
    """Records (title, fingerprint) for every alerting.send_alert call made
    through daily_pipeline's imported reference to it, without needing
    ALERT_WEBHOOK_URL configured or making any real HTTP call."""
    sent = []
    monkeypatch.setattr(
        daily_pipeline.alerting, "send_alert", lambda title, detail, **k: sent.append((title, k.get("fingerprint")))
    )
    return sent


def _fixed_utc_instant(utc_noon_date):
    """A fake datetime whose .now(tz) always returns noon UTC on the given
    date, so run_pipeline_with_retries' weekend check sees a deterministic
    "current" instant regardless of when the test suite actually runs."""

    class _Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(utc_noon_date.year, utc_noon_date.month, utc_noon_date.day, 12, 0, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz else base

    return _Fixed


class TestWeekendSkip:
    def test_saturday_in_ist_is_skipped_without_running_or_locking(self, db, monkeypatch):
        from datetime import date

        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(date(2026, 1, 17)))  # a Saturday
        ran = []
        monkeypatch.setattr(daily_pipeline, "run_pipeline", lambda dry_run=False: ran.append(1))

        assert daily_pipeline.run_pipeline_with_retries() is True
        assert ran == []  # never even attempted


class TestLock:
    def test_second_concurrent_run_is_refused(self, db, monkeypatch):
        from datetime import date

        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(date(2026, 1, 14)))  # a Wednesday
        ran = []
        monkeypatch.setattr(daily_pipeline, "run_pipeline", lambda dry_run=False: ran.append(1))

        # A genuinely separate connection (not the SAVEPOINT-shared test
        # connection) holds the same advisory lock key, simulating a truly
        # concurrent second process -- real cross-connection contention, not
        # a mock standing in for it.
        other_conn = engine.connect()
        held = other_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": daily_pipeline.PIPELINE_LOCK_KEY}
        ).scalar_one()
        assert held is True
        try:
            assert daily_pipeline.run_pipeline_with_retries() is False
            assert ran == []  # refused before ever calling run_pipeline
        finally:
            other_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": daily_pipeline.PIPELINE_LOCK_KEY})
            other_conn.close()


class TestRetry:
    def test_succeeds_on_a_later_attempt_after_transient_failures(self, db, monkeypatch):
        from datetime import date

        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(date(2026, 1, 14)))  # a Wednesday
        sleeps = []
        monkeypatch.setattr(daily_pipeline.time, "sleep", lambda seconds: sleeps.append(seconds))

        attempts = []

        def flaky(dry_run=False):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")

        monkeypatch.setattr(daily_pipeline, "run_pipeline", flaky)

        assert daily_pipeline.run_pipeline_with_retries() is True
        assert len(attempts) == 3
        assert sleeps == [300, 900]  # backoff before attempt 2, then before attempt 3

    def test_gives_up_after_max_attempts_and_reports_failure(self, db, monkeypatch, alerts):
        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(date(2026, 1, 14)))  # a Wednesday
        monkeypatch.setattr(daily_pipeline.time, "sleep", lambda seconds: None)

        attempts = []

        def always_fails(dry_run=False):
            attempts.append(1)
            raise RuntimeError("still broken")

        monkeypatch.setattr(daily_pipeline, "run_pipeline", always_fails)

        assert daily_pipeline.run_pipeline_with_retries() is False
        assert len(attempts) == daily_pipeline.PIPELINE_MAX_ATTEMPTS == 3

        # Exactly one alert for the whole invocation, not one per attempt.
        assert len(alerts) == 1
        title, fingerprint = alerts[0]
        assert title == "daily_pipeline failed"
        assert fingerprint == "daily_pipeline:failed"


class TestRowCountAlert:
    """most_recent_trading_day resolves off `datetime.now()`, so pin "now" to
    a real weekday that's guaranteed to have zero real daily_prices rows in
    the shared dev DB (far future) -- lets these tests control the exact row
    count instead of depending on whatever real data already exists."""

    TRADE_DATE = date(2099, 3, 2)  # a Monday

    @pytest.fixture(autouse=True)
    def _pin_now(self, monkeypatch):
        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(self.TRADE_DATE))
        monkeypatch.setattr(daily_pipeline, "run_pipeline", lambda dry_run=False: None)

    def _seed_prices(self, db, count):
        for i in range(count):
            inst = Instrument(symbol=f"ROWCNT{i}", exchange="NSE", company_name=f"Row Count Co {i}", is_active=True)
            db.add(inst)
            db.flush()
            price = Decimal("100")
            db.add(
                DailyPrice(
                    instrument_id=inst.id, trade_date=self.TRADE_DATE, open=price, high=price, low=price,
                    close=price, adjusted_close=price, volume=1000,
                )
            )
        db.flush()

    def test_alerts_when_row_count_is_below_threshold(self, db, alerts):
        self._seed_prices(db, 5)  # far below the 1000 threshold

        assert daily_pipeline.run_pipeline_with_retries() is True  # pipeline itself still "succeeded"
        assert len(alerts) == 1
        title, fingerprint = alerts[0]
        assert title == "daily_pipeline: suspiciously few rows ingested"
        assert fingerprint == "daily_pipeline:low_row_count"

    def test_does_not_alert_when_row_count_is_healthy(self, db, alerts):
        self._seed_prices(db, 1200)

        assert daily_pipeline.run_pipeline_with_retries() is True
        assert alerts == []


class TestApplyCorporateActionsTiming:
    """Regression coverage for the ex_date<=today gate in
    step_apply_corporate_actions: an action discovered well before its
    ex_date (ingest_corporate_actions looks up to 90 days ahead) must not be
    applied until daily_prices actually has every row it needs to adjust.
    Applying early would permanently mark it done while later-arriving rows
    for dates still before ex_date are silently skipped forever -- exactly
    the bug this test guards against (see app/jobs/repair_price_adjustments.py
    for the one-off repair of data affected before this gate existed)."""

    TODAY = date(2026, 3, 10)  # a Tuesday

    @pytest.fixture(autouse=True)
    def _pin_now(self, monkeypatch):
        monkeypatch.setattr(daily_pipeline, "datetime", _fixed_utc_instant(self.TODAY))

    def _seed(self, db, ex_date):
        inst = Instrument(symbol="TIMING1", exchange="NSE", company_name="Timing Co", is_active=True)
        db.add(inst)
        db.flush()
        action = CorporateAction(
            instrument_id=inst.id, ex_date=ex_date, action_type="SPLIT",
            ratio_from=1, ratio_to=5, applied=False,
        )
        db.add(action)
        db.flush()
        return inst, action

    def test_action_with_future_ex_date_is_not_applied_yet(self, db):
        inst, action = self._seed(db, ex_date=self.TODAY + timedelta(days=30))

        result = daily_pipeline.step_apply_corporate_actions(dry_run=False)

        assert result == "nothing pending"
        db.refresh(action)
        assert action.applied is False

    def test_action_with_ex_date_today_or_past_is_applied(self, db):
        inst_today, action_today = self._seed(db, ex_date=self.TODAY)
        db.add(DailyPrice(
            instrument_id=inst_today.id, trade_date=self.TODAY - timedelta(days=1),
            open=100, high=100, low=100, close=100, adjusted_close=100, volume=1000,
        ))
        db.flush()

        result = daily_pipeline.step_apply_corporate_actions(dry_run=False)

        assert "applied 1 action" in result
        db.refresh(action_today)
        assert action_today.applied is True


class TestDbConnectionFailureAtStartup:
    def test_main_alerts_and_returns_1_when_lock_acquisition_cannot_reach_db(self, monkeypatch, alerts):
        from sqlalchemy.exc import OperationalError

        def boom():
            raise OperationalError("SELECT pg_try_advisory_lock(...)", {}, Exception("connection refused"))

        monkeypatch.setattr(daily_pipeline, "run_pipeline_with_retries", boom)

        exit_code = daily_pipeline.main(["run"])

        assert exit_code == 1
        assert len(alerts) == 1
        title, fingerprint = alerts[0]
        assert title == "daily_pipeline: database connection failed at startup"
        assert fingerprint == "daily_pipeline:db_connection_failed"
