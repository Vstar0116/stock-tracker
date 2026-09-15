# Project: Indian Stock Market Tracking Application (Step 1)

Private stock tracker, 4–5 users, NSE/BSE equities. No trading, no order placement, no investment advice.

## Step 1 scope
- Daily EOD price pipeline (whole market)
- Technical indicators
- Screening against saved rules
- Per-user watchlists
- Daily alerts on screen matches
- Charting via TradingView free widget

## Out of scope
- Order placement / broker integration
- Buy/sell recommendations
- Document summarisation / RAG
- Full-market fundamentals ingestion (manual entry only)
- Social / public sharing features
- Multi-tenancy, billing, public sign-up

## Architecture
- Reads only from our own DB; never call an external API on the request path
- Heavy work happens in scheduled jobs after market close
- SQL answers numeric questions; an LLM only translates NL to SQL, never answers
- Prices must be corporate-action adjusted

## Stack
- PostgreSQL, Python 3.11+/FastAPI, pandas, React (Vite)
- systemd timers for jobs (`deploy/systemd/`) — not cron/Airflow

## Data sources
- Prices/instruments: NSE/BSE bhavcopy + symbol master
- Fundamentals: manual entry only
- No live/intraday prices

## Conventions
- Prefer CLI commands over MCP calls
- Type hints everywhere; Pydantic for API request/response shapes
- Alembic migrations only, never hand-edit tables
- Money/price as NUMERIC, never float
- Dates as DATE; timestamps as TIMESTAMPTZ, stored UTC
- Ingestion jobs must be idempotent

## Security checklist
Check on every change touching an endpoint, query, auth, config, or dependency.

1. Auth required on every non-public route
2. IDOR: scope id-fetched resources to the requesting user
3. Secrets from env only — never logged, returned, or committed
4. Parameterised SQL/ORM only, no string-built SQL (incl. LLM-generated screens)
5. No `dangerouslySetInnerHTML` / unescaped user text as markup
6. No cookie-only auth on state-changing requests without a CSRF check
7. CORS: explicit allowlist, never `*` + credentials
8. Validate upload type/size; never trust client filename or path
9. JWT: strong secret, short expiry, pinned algorithm
10. Reset/OTP tokens: single-use, time-limited, `secrets`-generated, rate limited
11. No password hashes/tokens/connection strings in responses, logs, or alerts
12. Rate limit login and expensive/external-backed endpoints
13. No hardcoded credentials anywhere
14. Keep dependencies pinned; check advisories before adding one
15. HSTS, X-Content-Type-Options, frame-ancestors, CSP on every response

## Response style
Terse. No filler. Three to six word sentences.
