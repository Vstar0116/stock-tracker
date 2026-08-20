# Deployment

Recommended host: **Render**. At ~30 users with no need for autoscaling, the
three real choices were Render, Fly.io, and Railway. Render wins here for the
same reason `deploy/systemd/*.timer` beat plain cron in the original scheduling
decision (see README "Scheduling"): the whole stack -- API, three cron jobs,
static frontend, and Postgres -- is one declarative `render.yaml` Blueprint,
checked into the repo, instead of state clicked together by hand in a
dashboard. Fly.io has no native managed Postgres offering left (it now
points you at a third-party partner) and no first-class cron job type -- you'd
fake one with a scheduled Machine. Railway's cron support and infra-as-code
are both newer and less complete. Render's tradeoff: cron schedules are
UTC-only (no `TimeZone=` like systemd), so `render.yaml` hand-converts 20:30
IST to `15:00 UTC` in a comment instead of declaring it -- the one thing
systemd did more cleanly.

## Required environment variables

Set these in the Render dashboard (`sync: false` vars in `render.yaml` --
never commit them) on **every** service that needs them (web + both cron
jobs share the DB and secret key):

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Wired automatically from the `stock-tracker-db` Postgres resource via `fromDatabase` in `render.yaml` -- don't set by hand. |
| `JWT_SECRET_KEY` | yes | `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Same value on the web service and both cron services (cron jobs don't issue tokens, but they share `app.config.Settings`, which requires it to start at all). |
| `APP_ENV` | yes | `production` -- set in `render.yaml`. Enables the localhost-`DATABASE_URL` guard in `app/config.py`. |
| `CORS_ORIGINS` | yes (web only) | The frontend's real Render URL (or custom domain), comma-separated if more than one. Set in `render.yaml`; update it if you attach a custom domain. |
| `WEB_CONCURRENCY` | no | Gunicorn worker count, default `2`. See `entrypoint.sh` for the reasoning. |
| `GROQ_API_KEY` | no | Enables `POST /api/screens/from-text`. Leave unset to disable the feature entirely (it 422s cleanly). |
| `NL_SCREEN_MODEL` | no | Default `openai/gpt-oss-120b`. |
| `ALERT_WEBHOOK_URL` | no | Slack-compatible webhook for pipeline-failure alerts (see README "Alerting"). Strongly recommended in production -- without it, a broken pipeline fails silently. |
| `VITE_API_BASE_URL` | yes (frontend build only) | Baked into the built JS at **build time**, not read at runtime -- see "Frontend" below. |
| `BACKUP_S3_BUCKET` | yes (backup cron only) | Bucket the daily off-host backup uploads to. See "Backups & recovery" below. |
| `BACKUP_S3_ENDPOINT_URL` | no | Leave unset for real AWS S3. Set for Cloudflare R2 / Backblaze B2 / DigitalOcean Spaces / any other S3-compatible store. |
| `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY` | yes (backup cron only) | Credentials for the bucket above. Use a key scoped to just that bucket, not a root/admin key. |

## First-time setup

1. Push this repo to GitHub/GitLab and connect it to Render.
2. In the Render dashboard, create a new **Blueprint** from the repo -- it
   reads `render.yaml` and proposes the Postgres database, the API web
   service, the three cron jobs, and the static frontend site as one unit.
3. Before applying, fill in the `sync: false` secrets it prompts for:
   `JWT_SECRET_KEY` (generate one, see table above) on the web service and
   all three cron services, `GROQ_API_KEY` / `ALERT_WEBHOOK_URL` if you want
   those features on, and the `BACKUP_S3_*` variables (see "Backups &
   recovery" below) so the daily backup has somewhere to write to.
4. Apply the blueprint. Render provisions Postgres first, then builds and
   deploys the web service.
5. **Deploy the web service before the cron jobs' first scheduled run.**
   Its startup runs `alembic upgrade head` (see "Migrations" below), which
   creates the schema from nothing. The cron jobs also run `alembic upgrade
   head` defensively before their own command, so this isn't a hard
   ordering requirement, just the first Alembic run should happen once,
   which the web service's normal deploy already guarantees.
6. Confirm the API is healthy: `curl https://stock-tracker-api.onrender.com/health`
   should return `{"status":"ok"}`.
7. Create your first user (see "Creating users" below) and log into the
   frontend at `https://stock-tracker-frontend.onrender.com`.
8. Confirm the freshness box in the nav / the `/status` admin page shows
   real data once the first scheduled pipeline run has happened (or trigger
   one manually -- see "Deploying an update" below for how to run a one-off
   command against a live service).

## Migrations run as part of every deploy

`entrypoint.sh` is the web service's container command (`CMD` in the
`Dockerfile`):

```sh
set -e
alembic upgrade head
exec gunicorn ...
```

`set -e` means: if `alembic upgrade head` exits non-zero, the script (and
the container) exits non-zero before gunicorn ever starts. Render's
zero-downtime deploy only cuts traffic over to a new instance once it passes
`healthCheckPath: /health` -- a container that never starts gunicorn never
passes that check, so **Render marks the deploy failed and leaves the
previous, still-schema-consistent instance serving traffic.** There is no
window where a half-migrated database is live. This is also why migrations
are NOT run from a separate one-off "release phase" step some platforms
offer -- Render doesn't have one, and running it inline in the same
container startup that gunicorn depends on gets the same guarantee for
free, with less moving infrastructure.

The cron jobs additionally run `alembic upgrade head &&` in front of their
own command (see `render.yaml`) -- defensive and idempotent, not the primary
mechanism, so a scheduled run can never execute against a stale schema
regardless of deploy ordering.

## Scheduling

Same jobs, same cadence as the original systemd-timer design (README
"Scheduling" / "Alerting"), reimplemented as Render cron services since
there's no long-lived host to run `systemd` on:

- `stock-tracker-daily-pipeline` -- `python -m app.jobs.daily_pipeline`,
  `0 15 * * 1-5` (20:30 IST, Mon-Fri, converted to UTC by hand since Render
  cron has no `TimeZone=` field).
- `stock-tracker-healthcheck` -- `python -m app.jobs.healthcheck`, hourly.
  Independent of the pipeline's own schedule for the same reason as before:
  if the thing that's supposed to trigger the pipeline stops firing, only
  an outside watcher notices.
- `stock-tracker-backup` -- `python -m app.jobs.backup_db`, daily at 21:30
  IST (`0 16 * * *`, every day including weekends, since user data can
  change any day, unlike market data). See "Backups & recovery" below.

All three use the exact same Docker image as the API (`dockerfilePath:
./Dockerfile`), just with `dockerCommand` overriding the image's default
`CMD` -- one image for the whole app, no separate "jobs" image to keep in
sync.

## Backups & recovery

### Does Render back up the database by default?

**Yes, but with caveats that matter for this app.** Render continuously
backs up paid Postgres instances for point-in-time recovery (PITR) --
Free-tier databases get none at all. Retention is tied to your **Render
workspace/account plan**, not the database's own instance size:

| Workspace plan | PITR retention |
|---|---|
| Hobby | past 3 days |
| Pro or higher | past 7 days |

Restoring creates a **new** database instance (not in-place), from the
dashboard's Recovery page: name the new instance, pick a target timestamp
(must be more than 10 minutes in the past), wait for it to go from
"Recovery In Progress" to "Available", verify the data, then point
`DATABASE_URL` at it and retire the old instance.

That's real and it's the first line of defense -- but it has two gaps for
this app specifically: **retention is only 3-7 days**, and **the backup
lives entirely inside Render's own control plane** -- if the Render account
itself is ever locked, suspended for billing, or deleted, every backup it
ever took goes with it. Neither gap is acceptable for data (user accounts,
watchlists, screens) that a person would have to manually recreate if lost.
That's what `stock-tracker-backup` (`app/jobs/backup_db.py`) exists to
close: a `pg_dump` of the tables Render's short retention window and
platform lock-in can't be trusted to protect, uploaded daily to a bucket
you control, independent of Render entirely.

**Both layers are documented here.** For anything within the last few days,
use Render's PITR -- it's a whole-database restore and needs no manual
runbook beyond the dashboard steps above. For anything older, or if the
Render account itself is unreachable, use the `backup_db` restore runbook
below.

### What's genuinely irreplaceable

| Table | Backed up? | Why |
|---|---|---|
| `users` | yes | No source but the humans who typed their accounts in. |
| `watchlists`, `watchlist_items` | yes | Ditto -- hand-curated by each user. |
| `screens` | yes | Hand-written (or NL-translated, but the *result* is never regenerated) screening rules. |
| `alerts` | yes | Historical record of what fired and when -- `snapshot` captures values at trigger time that later indicator recomputation would overwrite. |
| `fundamentals` | yes | **Manual entry only** (`CLAUDE.md`: "Fundamentals: manual entry only for now") -- unlike prices, there is no ingestion job that can regenerate this if it's lost. Easy to overlook precisely because it's a quiet, low-traffic table. |
| `instruments` | yes, but for a different reason | Its *content* (symbol, company name, sector) is reconstructible any time by re-running `ingest_instruments` against NSE/BSE. It's backed up anyway because it's a surrogate-key table and `watchlist_items` / `alerts` / `fundamentals` store FKs to its exact `id` values -- a fresh re-ingest would assign new IDs, not the old ones, silently orphaning every reference in the tables above it. Backing it up turns restore into one deterministic `pg_restore`, with no manual ID-remapping step to improvise under pressure. |
| `daily_prices`, `indicators`, `corporate_actions` | **no** | Fully reconstructible: `backfill_prices` / `ingest_prices` re-pull NSE/BSE bhavcopy for any date range, `compute_indicators` recomputes deterministically from prices, `ingest_corporate_actions` re-pulls from NSE/BSE's own corporate actions APIs. Every one of these jobs is already idempotent (`CLAUDE.md`: "every ingestion job must be idempotent"), so re-running them is the actual, cheaper recovery path -- including these tables in the daily backup would make the dump orders of magnitude larger for data that's never actually at risk of being unrecoverable. |
| `job_runs` | no | Operational audit trail, not user data. Losing history here is a minor inconvenience (you can't see *last week's* pipeline runs), not a correctness problem -- covered by Render's own PITR for the short term, not worth a separate durable copy. |

### What our recovery point objective actually is

Stated plainly, in the worst case:

- **`backup_db`-covered tables** (users, watchlists, watchlist_items,
  screens, alerts, fundamentals, instruments): up to **24 hours** of loss --
  the gap between the last daily backup (21:30 IST) and the moment of
  failure. If the outage happens at 21:00 IST tomorrow, you lose almost a
  full day of watchlist edits, new screens, and alert history.
- **Render's PITR**, if the account itself is intact and reachable: loss is
  close to zero (continuous WAL-based backup), for anything within the
  retention window (3-7 days per the table above). This is the better RPO
  *when it's available* -- prefer it for a recent-and-Render-is-fine
  incident, fall back to `backup_db`'s daily dump only when PITR's window
  has passed or the Render account itself is the thing that's gone.
- **`daily_prices` / `indicators` / `corporate_actions`**: effectively
  **zero acceptable loss**, because "loss" isn't the right frame -- these
  are recomputed from NSE/BSE source data, not restored from a point in
  time. `backfill_prices` can re-pull any date range, so a total loss of
  these tables costs re-ingestion time (hours, bounded by how far back you
  backfill), not unrecoverable data.

**Is a 24-hour RPO on the irreplaceable tables acceptable?** Yes, for this
app's actual usage pattern: 4-5(-30) known users editing watchlists and
screens occasionally, not a transactional system processing continuous
writes. Losing a day of "I added TCS to my watchlist" is an annoyance a
user re-does in a minute, not a business-critical loss. If that judgment
changes (e.g. screens become the primary way alerts get configured and
losing a day of edits is genuinely costly), the fix is tightening
`backup_db`'s schedule (e.g. every 6 hours) -- cheap to do since the dump
itself is small (see "Restore procedure" below for the actual size
observed) -- not a bigger architectural change.

### Restore procedure (`backup_db` path)

Precise enough to follow without improvising. Run every command as the
database's admin user (`pg_restore --disable-triggers` requires table-owner
or superuser privileges).

1. **Get a target database with the current schema.** Either a fresh Render
   Postgres instance, or the existing one if it's just the data (not the
   whole instance) that's gone. Either way:
   ```bash
   alembic upgrade head
   ```
   This creates every table, including `instruments`, empty. Do **not**
   skip this in favor of trying to restore schema from the backup file --
   Alembic is the single source of truth for schema (`CLAUDE.md`: "Alembic
   for database migrations -- never edit tables by hand"), and the backup
   is a data-only dump (see below) that doesn't contain one.

2. **Find the backup to restore.** List what's in the bucket, newest last:
   ```bash
   aws s3 ls s3://<bucket>/stock-tracker-backups/ --recursive
   # or, for a non-AWS S3-compatible store:
   aws s3 --endpoint-url <BACKUP_S3_ENDPOINT_URL> ls s3://<bucket>/stock-tracker-backups/ --recursive
   ```

3. **Download it:**
   ```bash
   aws s3 cp s3://<bucket>/stock-tracker-backups/stock-tracker-<TIMESTAMP>.dump ./restore.dump
   ```

4. **Restore.** This is a data-only dump of `instruments`, `users`,
   `watchlists`, `watchlist_items`, `screens`, `alerts`, `fundamentals` --
   `--disable-triggers` is required (not optional) because these tables
   have foreign keys to each other and pg_restore does not topologically
   sort a data-only restore by dependency; it temporarily disables the
   triggers Postgres uses internally to enforce FKs during the load, then
   they're back in effect for normal operation immediately after:
   ```bash
   pg_restore --data-only --disable-triggers \
     --dbname "$DATABASE_URL" \
     ./restore.dump
   ```
   A clean restore exits 0 with no output. If you see `unrecognized
   configuration parameter` errors, your `pg_dump`/`pg_restore` client
   version doesn't match the target server's major version -- see "A real
   bug this verification caught" below before assuming data was lost; check
   actual row counts (step 5) either way.

5. **Verify.** At minimum, row counts:
   ```sql
   SELECT 'instruments', count(*) FROM instruments
   UNION ALL SELECT 'users', count(*) FROM users
   UNION ALL SELECT 'watchlists', count(*) FROM watchlists
   UNION ALL SELECT 'watchlist_items', count(*) FROM watchlist_items
   UNION ALL SELECT 'screens', count(*) FROM screens
   UNION ALL SELECT 'alerts', count(*) FROM alerts
   UNION ALL SELECT 'fundamentals', count(*) FROM fundamentals;
   ```
   and confirm a real login works against a restored `users` row.

6. **Repopulate the reconstructible tables** (if this was a total-loss
   recovery, not just a restore of the irreplaceable tables onto an
   otherwise-intact DB):
   ```bash
   python -m app.jobs.ingest_instruments   # only if instruments weren't part of the restore -- normally they were (step 4)
   python -m app.jobs.backfill_prices --from 2020-01-01 --to <today>   # however far back you want price history
   python -m app.jobs.compute_indicators
   python -m app.jobs.run_screens   # optional -- re-evaluates screens against restored history
   ```

### Backup was actually tested -- what was run and what it proved

Ran the full loop for real, end to end, in isolated Docker containers (not
mocked): an ephemeral source Postgres seeded with representative rows
across every backed-up table (including FK relationships -- a watchlist
item pointing at a real instrument, an alert pointing at a real screen and
instrument) → the real `stock-tracker-api` image running
`python -m app.jobs.backup_db` against it, uploading to a real MinIO
instance (S3-compatible, standing in for the production bucket) → a
completely separate, empty scratch Postgres, migrated with `alembic upgrade
head` and nothing else → the backup downloaded and restored into it with
the exact `pg_restore` command in step 4 above.

**Result:**
```
$ python -m app.jobs.backup_db
backup uploaded: s3://verify-backups/stock-tracker-backups/stock-tracker-20260820T161310Z.dump

$ aws s3 ls s3://verify-backups/stock-tracker-backups/ --recursive
2026-08-20 16:13      22213 stock-tracker-backups/stock-tracker-20260820T161310Z.dump

$ pg_restore --data-only --disable-triggers --dbname "$DATABASE_URL" ./restore.dump
$ echo $?
0
```

Every row compared field-by-field between source and restored database --
including the bcrypt `password_hash`, the JSONB `screens.definition` and
`alerts.snapshot` payloads, and the `date` column -- came back **identical**:

```
instruments: [{'id': 1, 'symbol': 'VERIFYA', ...}, {'id': 2, 'symbol': 'VERIFYB', ...}]
users: [{'id': 1, 'email': 'restore-verify@example.com', 'password_hash': '$2b$12$1FDQ...', ...}]
watchlists: [{'id': 1, 'user_id': 1, 'name': 'Verification List'}]
watchlist_items: [{'id': 1, 'watchlist_id': 1, 'instrument_id': 1, 'notes': 'seeded for restore verification'}, ...]
screens: [{'id': 1, ..., 'definition': {'op': 'gt', 'type': 'compare', 'field': 'close', 'value': 100}, ...}]
alerts: [{'id': 1, ..., 'snapshot': {'close': 123.45, 'rsi_14': 61.2}, ...}]
```
Identical in both databases, and a live join query against the restored
database resolved the foreign keys correctly (`watchlist_items` → real
`instruments` rows, `alerts` → real `screens`/`instruments` rows) --
confirming the restore isn't just "the right bytes landed," the data is
actually usable by the app as-is.

### A real bug this verification caught

The first attempt at this restore produced errors:
```
pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"
pg_restore: warning: errors ignored on restore: 1
```
The image's base (`python:3.11-slim`, Debian "trixie") installs
`postgresql-client` version **17** by default from Debian's own repo --
newer than the Postgres **16** server this app actually runs
(`docker-compose.yml`, and `render.yaml`'s `postgresMajorVersion: "16"`).
`pg_dump` 17 emits a `SET transaction_timeout = 0` that a PG16 server
doesn't understand. Row counts afterward showed the data had in fact loaded
correctly (`--disable-triggers` restores continue past a single failed
`SET`) -- so this specific case never actually lost data. But a restore
that prints `error:` lines during a real incident is not something an
operator under pressure should have to judge as harmless; a clean restore
should print nothing. Fixed by pinning the Dockerfile to install
`postgresql-client-16` specifically from the official PGDG apt repository
(matching the server exactly), confirmed by re-running the whole loop above
with the rebuilt image: zero warnings, exit code 0. See the Dockerfile's
comment on that `RUN apt-get install` line for the full reasoning -- this
is exactly the kind of gap "verify recovery, don't assume" is meant to
catch, and it did.

## Frontend

Vite reads `VITE_API_BASE_URL` at **build** time (`frontend/src/lib/api.ts`)
and inlines it into the built JS as a static string -- it is not an
environment variable the running site reads, so it must be set as a
**build-time** env var on the static site service (`render.yaml`'s
`envVars` under `stock-tracker-frontend`), pointing at the API service's
real URL. If you change the API's URL (custom domain, etc.), you must
rebuild the frontend, not just restart it.

The `routes: [{type: rewrite, source: /*, destination: /index.html}]` entry
is required because `App.tsx` uses `BrowserRouter` (client-side routing) --
without it, refreshing on `/watchlists` or sharing a deep link 404s instead
of loading `index.html` and letting React Router take over.

## Creating users

There's no self-registration endpoint by design (`app/jobs/create_user.py`'s
docstring: "this CLI, run by whoever administers the app, is the only way to
add one"). On Render, run it via the web service's **Shell** tab in the
dashboard (or `render ssh stock-tracker-api` with the Render CLI), which
gives you an interactive terminal in a running instance with `DATABASE_URL`
already set:

```bash
python -m app.jobs.create_user --email newuser@example.com --name "New User"
```

It prompts for a password interactively (with confirmation) so it never
lands in shell history, a process listing, or a Render log line.

Shell access requires a paid instance plan (the `starter` plan in
`render.yaml` qualifies). If you're on a plan without Shell access, the
fallback is running the same command locally with `DATABASE_URL` pointed at
the production database via an environment variable for that one
invocation -- treat the production connection string with the same care as
any other production credential, and unset it again immediately after.

## Deploying an update

Push to the branch Render is watching -- it rebuilds the Docker image, runs
`alembic upgrade head` inside the new container (see "Migrations" above),
and only cuts traffic over once `/health` passes. The frontend static site
rebuilds independently on the same push if `frontend/` changed.

For a one-off manual deploy (e.g. re-deploying without a new commit, or
picking up a dashboard-only env var change), use "Manual Deploy" in the
Render dashboard or `render deploys create` with the CLI.

## Rolling back

Render keeps deploy history per service; "Rollback" in the dashboard
redeploys a previous build/image. **This rolls back application code only,
not the database schema.** If the deploy you're rolling back past included
an Alembic migration, rolling back the code without also reversing the
migration can leave older code running against a newer schema.

- If the bad deploy did **not** include a schema change: a plain dashboard
  rollback is sufficient.
- If it **did**: also run `alembic downgrade -1` (via the Shell tab, same
  access as "Creating users" above) against the production database
  *before or immediately after* the code rollback, so the running code and
  the schema agree. There's no automated coupling between "roll back the
  app" and "roll back the schema" here -- for a team this size, a documented
  manual step is the right amount of process, not a bespoke migration
  orchestrator.
