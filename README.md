# Stock Tracker

[![CI](https://github.com/Vstar0116/stock-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Vstar0116/stock-tracker/actions/workflows/ci.yml)

Private Indian stock market tracking application (Step 1 skeleton — see `CLAUDE.md` for scope).

## Setup

1. Copy the env file and generate a JWT secret (the app refuses to start without
   one — see [Configuration](#configuration) below):

   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Paste the generated value onto the `JWT_SECRET_KEY=` line in `.env`.

2. Start Postgres:

   ```bash
   docker compose up -d
   ```

3. Install dependencies (Python 3.11+):

   ```bash
   python -m venv .venv
   .venv/Scripts/activate   # Windows
   pip install -e ".[dev]"
   ```

4. Run database migrations:

   ```bash
   alembic upgrade head
   ```

5. Create a user (no self-registration endpoint — this is the only way in):

   ```bash
   python -m app.jobs.create_user --email you@example.com --name "Your Name"
   ```

6. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

7. Check it's alive:

   ```bash
   curl http://localhost:8000/health
   ```

8. Start the frontend (separate terminal):

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

   Open http://localhost:5173 and log in with the user created in step 5.

## Configuration

All settings are read from the environment (`.env` locally) -- see `.env.example`
for the full list with comments. Two are enforced at startup, not just documented:

- `JWT_SECRET_KEY` -- **required, no default.** Signs login JWTs; the app refuses
  to start without it, and refuses a value under 32 bytes. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `DATABASE_URL` -- defaults to the local docker-compose Postgres for ergonomic
  local dev. If `APP_ENV=production` and this is still pointing at localhost,
  the app refuses to start -- set it to the real production database first.

Everything else (`JWT_EXPIRE_MINUTES`, `APP_ENV`, `GROQ_API_KEY`,
`NL_SCREEN_MODEL`) is optional with a working default.

## Optional: natural-language screen creation

`POST /api/screens/from-text` translates a plain-English description (e.g.
"pharma stocks below their 200 day average with RSI under 40") into a screen
rule tree, via Groq's free OpenAI-compatible API (`openai/gpt-oss-120b`).
Purely a translation step -- the LLM never sees market data and its output is
always revalidated against our Pydantic schema before it's returned, so a bad
translation 422s instead of silently becoming a screen.

Get a free key at https://console.groq.com/keys and set `GROQ_API_KEY` in
`.env`. Leave it unset to disable the feature -- everything else works fine
without it.

## Tests

```bash
pytest
```

## Creating a migration

```bash
alembic revision --autogenerate -m "description"
```

## Scheduling

`app/jobs/daily_pipeline.py` (ingest prices, adjust for corporate actions,
recompute indicators, run screens) needs to run once every trading evening.
Scheduled via a **systemd timer** (`deploy/systemd/`), not cron or a hosted
scheduler:

- This deploys to a single, always-on Linux host (see `deploy/systemd/*.service`'s
  `WorkingDirectory`/venv path assumption) -- no container orchestrator or PaaS
  is in the picture, so a hosted scheduler (Render Cron, GitHub Actions
  `schedule:`, etc.) isn't applicable, and a container-cron sidecar would mean
  containerizing the app first for no real benefit at this scale.
- Over plain cron: a timer's `TimeZone=` is declarative and enforced by
  systemd itself, instead of hand-converting IST to UTC in a comment next to
  a bare `30 13 * * 1-5` and hoping the host's system timezone matches the
  assumption that conversion was based on. `journalctl -u stock-daily-pipeline`
  is also a real log, and `Persistent=true` catches a run that was missed
  because the host was down at trigger time -- cron does neither for free.

Install (both the pipeline and its healthcheck -- see "Alerting" below):

```bash
sudo cp deploy/systemd/stock-daily-pipeline.* deploy/systemd/stock-healthcheck.* /etc/systemd/system/
# edit WorkingDirectory / ExecStart / EnvironmentFile in both .service files first
sudo systemctl daemon-reload
sudo systemctl enable --now stock-daily-pipeline.timer stock-healthcheck.timer
```

**Schedule: 20:30 IST, Monday-Friday** (`deploy/systemd/stock-daily-pipeline.timer`).
NSE close is 15:30 IST, but there's no published SLA for when the bhavcopy
actually becomes available, and secondary sources disagree by hours (informal
reports range from ~16:30 to ~20:00 IST). Rather than trust that, this is
anchored on real first-party data: both NSE's and BSE's bhavcopy for the day
were directly confirmed already published by 20:10 IST. 20:30 sits past that
observation and past the latest third-party estimate found. The pipeline's own
retry-with-backoff (3 attempts, 5 then 15 minutes apart, see
`daily_pipeline.run_pipeline_with_retries`) is the backstop for an unusually
late day beyond that -- it isn't padding the base schedule further, which
would just delay indicators/screens/alerts for everyone on every normal day
to cover a rare slow one.

Weekends are skipped at two layers: the timer's `OnCalendar=Mon..Fri` never
fires the job at all, and `run_pipeline_with_retries()` checks the IST
calendar date itself before doing anything (catches a manual invocation or a
misconfigured timer). Exchange holidays are deliberately **not** hardcoded
anywhere -- `ingest_prices.py` already treats "bhavcopy not published" as a
clean skip rather than a failure, so a holiday run just finishes as a normal
success with nothing new to ingest, with no calendar to go stale.

Concurrency: `run_pipeline_with_retries()` takes a Postgres advisory lock for
the whole run (all retries), so two runs (a scheduled one and a manual
`python -m app.jobs.daily_pipeline`, say) can't overlap -- the second exits
immediately instead of racing the first. The lock is session-scoped, so a
killed/crashed process releases it automatically; there's no lock row that
can get stuck and need manual clearing.

Check what actually ran, and whether the data is current, with:

```bash
python -m app.jobs.daily_pipeline status
```

## Alerting

With nobody watching a dashboard, a broken pipeline otherwise means stale
data nobody notices for days. `app/services/alerting.py` sends a webhook
alert (Slack-compatible -- Slack, Discord's Slack-compatible shim, a Teams
connector, or a custom `{"text": "..."}` receiver) when:

- the daily pipeline fails (all 3 retry attempts exhausted)
- the pipeline succeeds but ingested suspiciously few rows (< 1,000
  `daily_prices` rows for the day -- NSE's own EQ-series bhavcopy alone is
  normally ~2,500+, so this is well under half of just one exchange's
  typical contribution, not normal day-to-day variance)
- the pipeline hasn't run at all in over 36 hours (`app/jobs/healthcheck.py`,
  its own independent systemd timer, `stock-healthcheck.timer` -- has to be
  separate from the pipeline's own schedule, since if the pipeline's trigger
  itself stops firing, nothing inside the pipeline can notice that)
- a database connection fails when either job starts up

Set `ALERT_WEBHOOK_URL` to enable it (see `.env.example`). Unset, it's a
complete no-op -- local dev is unaffected. Repeats of the *same* underlying
problem are suppressed for 6 hours so a retrying job or an hourly
healthcheck doesn't spam the same message; a genuinely different problem (or
the same one recurring the next day) always alerts.

Alert text is passed through a redaction filter before delivery, stripping
anything shaped like `scheme://user:pass@host` (confirmed necessary, not
theoretical -- a malformed `DATABASE_URL` raises a SQLAlchemy error whose
message echoes the raw connection string).

Confirm the wiring works without waiting for a real failure:

```bash
python -m app.services.alerting
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the production Dockerfile, Render
Blueprint (`render.yaml`), required environment variables, how to create
users / deploy / roll back, and the backup & disaster-recovery runbook.
