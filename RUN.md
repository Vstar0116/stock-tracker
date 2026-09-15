# Running the app locally

Everything in one place: first-time setup, starting it day-to-day, and getting
real data into it. See [README.md](README.md) for config details and
[DEPLOYMENT.md](DEPLOYMENT.md) for production.

Commands below are PowerShell (Windows). Git Bash equivalents differ only in
venv activation (`source .venv/Scripts/activate`).

## Prerequisites

- Docker Desktop (for Postgres)
- Python 3.11+
- Node.js 18+

## First-time setup

```powershell
# 1. Env file + JWT secret (app refuses to start without one)
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# paste the output onto JWT_SECRET_KEY= in .env

# 2. Postgres
docker compose up -d

# 3. Python deps
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 4. Schema
alembic upgrade head

# 5. A login user (no self-signup exists)
python -m app.jobs.create_user --email you@example.com --name "Your Name"

# 6. Frontend deps
cd frontend
npm install
cd ..
```

## Every time you work on it

Three terminals:

```powershell
# Terminal 1 — DB (if not already running)
docker compose up -d

# Terminal 2 — backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload

# Terminal 3 — frontend
cd frontend
npm run dev
```

Open http://localhost:5173 and log in with the user from setup step 5.
Backend health check: http://localhost:8000/health.

## Getting real data in

The app never fetches data live — everything comes from a scheduled pipeline
that hits NSE/BSE. Locally, nothing runs on a schedule, so run it by hand:

```powershell
# One day's worth (instruments, prices, corporate actions, indicators, screens)
python -m app.jobs.daily_pipeline

# See what happened / whether data is current
python -m app.jobs.daily_pipeline status
```

For real history (indicators like SMA-200 need ~200+ trading days to stop
showing null), backfill a date range first, then compute indicators over the
full history:

```powershell
python -m app.jobs.backfill_prices --from 2024-01-01 --to 2026-08-01
python -m app.jobs.compute_indicators
python -m app.jobs.ingest_corporate_actions
```

Safe to re-run any of these — every job is idempotent (upserts / ON CONFLICT
DO NOTHING), so re-running for a date already loaded is a cheap no-op.

## Tests

```powershell
pytest
```

## Optional: natural-language screening

Set `GROQ_API_KEY` in `.env` (free key at https://console.groq.com/keys) to
enable `POST /api/screens/from-text` and the "Generate rule" box on the
Screener page. Leave blank — everything else works without it.
