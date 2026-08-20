# Project: Indian Stock Market Tracking Application (Step 1)

## What this is
A private stock tracking application for 4–5 users covering Indian equities (NSE/BSE).
Not a public product. No trading, no order placement, no investment advice.

## Step 1 scope — build only these
- Daily price data pipeline (end-of-day, whole market)
- Technical indicator calculation
- Stock screening against saved rules
- Per-user watchlists
- Daily alerts when a saved screen matches
- Charting via TradingView's free embeddable widget

## Explicitly OUT of scope — do not build these
- Order placement or broker trading integration of any kind
- Buy/sell recommendations or signals presented as advice
- Document summarisation / RAG over filings or news
- Full-market fundamentals ingestion (manual entry only for now)
- Social feed or public sharing features
- Multi-tenancy, billing, or public sign-up

## Architecture principles
1. The app reads ONLY from our own database. Never call an external API while a
   user is waiting for a page to load.
2. All heavy work happens in scheduled jobs after market close.
3. Numeric questions are answered by SQL, never by an LLM guessing. If an LLM is
   involved in querying, its job is to TRANSLATE a question into SQL, not to answer it.
4. Prices must be corporate-action adjusted. An unadjusted series silently corrupts
   every moving average that spans a split or bonus issue.

## Stack
- PostgreSQL
- Python 3.11+ backend, FastAPI
- pandas for data processing
- React frontend (Vite)
- Scheduled jobs via cron (keep it simple — no Airflow at this scale)

## Data sources
- Prices: NSE/BSE daily bhavcopy files (free, full market, end-of-day)
- Instruments: exchange-published symbol master
- Fundamentals: manual entry only in Step 1
- Live/intraday prices: NOT in Step 1

## Conventions
- Type hints on all Python functions
- Pydantic models for all API request/response shapes
- Alembic for database migrations — never edit tables by hand
- All money/price values as NUMERIC in Postgres, never float
- All dates stored as DATE, timezone-aware timestamps as TIMESTAMPTZ (IST is the
  market timezone but store UTC)
- Every ingestion job must be idempotent: running it twice for the same day must
  not duplicate or corrupt data