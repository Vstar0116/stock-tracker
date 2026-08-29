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
- Scheduled jobs via systemd timers (no Airflow at this scale). Timers over cron:
  `TimeZone=` is declarative instead of hand-converting IST to UTC, `journalctl`
  gives real logs, and `Persistent=true` catches runs missed while the host was
  down. Units live in `deploy/systemd/`.

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

## Security checks — review every change against these
Run through this list before finishing any change that touches an endpoint, a
query, auth, config, or a dependency.

1. **Broken authentication** — every non-public route depends on the auth
   dependency; no endpoint is reachable without a valid token.
2. **IDOR / broken access control** — a resource fetched by id is always scoped
   to the requesting user. Never trust an id in a path or body as proof of
   ownership.
3. **Exposed API keys & secrets** — secrets come from the environment only.
   Never commit `.env`, never log a key, never return one in a response.
4. **SQL injection** — parameterised queries or the ORM, always. No f-strings
   or concatenation building SQL, including in LLM-translated screen rules.
5. **XSS** — no `dangerouslySetInnerHTML`, no unescaped user text rendered as
   markup. Treat symbol names and screen names as untrusted.
6. **CSRF** — state-changing requests must not authenticate via a cookie the
   browser attaches automatically without a matching anti-CSRF check.
7. **Insecure CORS** — explicit allowlist of origins. Never `*` combined with
   credentials, never reflect the request's `Origin` header.
8. **Insecure file uploads** — validate type and size, never trust the client
   filename, never write into a web-served path.
9. **JWT / session security** — signed with a strong secret, short expiry,
   algorithm pinned (reject `none` and algorithm confusion), no sensitive
   claims in the payload.
10. **Weak OTP & password reset logic** — reset tokens single-use,
    time-limited, generated with `secrets`, and rate limited. No user
    enumeration in the response.
11. **Sensitive data exposure** — no password hashes, tokens, or connection
    strings in API responses, logs, or alert payloads.
12. **Missing rate limiting** — login and any expensive or external-API-backed
    endpoint must be rate limited.
13. **Hardcoded credentials** — no passwords, tokens, or connection strings in
    source, tests, or fixtures. Config or environment only.
14. **Vulnerable dependencies** — keep pins current, check advisories before
    adding a dependency, prefer the standard library where it suffices.
15. **Missing security headers** — HSTS, `X-Content-Type-Options`,
    `X-Frame-Options`/frame-ancestors, and a CSP on every response.

## Response style
1. No filler.
2. Direct answers only.
3. Three to six word sentences.