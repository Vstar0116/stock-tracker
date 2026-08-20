# Stock Tracker

Private Indian stock market tracking application (Step 1 skeleton — see `CLAUDE.md` for scope).

## Setup

1. Copy the env file:

   ```bash
   cp .env.example .env
   ```

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

Nothing runs on its own — `app/jobs/daily_pipeline.py` (ingest prices, adjust for
corporate actions, recompute indicators, run screens) has to be scheduled on
whatever host runs this in production. `deploy/crontab` has the line
(weekdays, evening IST, after NSE/BSE typically publish the day's bhavcopy);
install it with `crontab deploy/crontab` after editing the paths inside it.

Check what actually ran, and whether the data is current, with:

```bash
python -m app.jobs.daily_pipeline status
```
