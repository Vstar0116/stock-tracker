# Deployment (actual production setup)

Production is a **home Fedora server**, reachable from the internet via
**Tailscale Funnel**, running the API as a **systemd service**
(`stock-api.service`) under gunicorn. There is no PaaS, no container
orchestrator, and (as of this writing) **no git checkout on the server** --
code is deployed by copying files over, not by cloning/pulling this repo.
The frontend is a separate static deploy on **Vercel**, auto-building from
GitHub pushes to `main`.

If you're looking for the original Render-based design (managed Postgres,
git-push deploys, atomic migration-gated cutover), see
[DEPLOYMENT.render.md](DEPLOYMENT.render.md) -- kept for reference, not
current.

## Topology

```
Internet --(Tailscale Funnel, HTTPS)--> tailscaled (Fedora) --> gunicorn :8000 --> Postgres (local)
                                                                     ^
LAN (192.168.1.x) -------------------------------------------------+   (also directly reachable --
                                                                         gunicorn binds 0.0.0.0, see
                                                                         "Known gaps" below)

Vercel (frontend static build) --(HTTPS, fetch + bearer token)--> Tailscale Funnel URL above
```

- Host alias: `fedora-server` in SSH config, resolves to `192.168.1.7`, user `mrvick`.
- App root: `/opt/stock-tracker`.
- Python env: `/opt/stock-tracker/.venv` (created once with `uv sync` or `pip install -e .`; not rebuilt on every deploy unless dependencies actually changed).
- systemd unit: `stock-api.service` (installed directly on the host -- **not currently checked into `deploy/systemd/`**; see "Known gaps").
- Public URL: the Tailscale Funnel hostname (`https://<tailnet-name>.<tailnet-id>.ts.net`) -- get the exact value with `tailscale funnel status` on the Fedora box, or from `frontend/.env`'s built value.

## Required environment variables

Set in `/opt/stock-tracker/.env` (mode `600`, owned by `mrvick`; never committed -- see `.env.example` for the full list with descriptions). The ones that matter most for this deployment specifically:

| Variable | Required | Notes |
|---|---|---|
| `APP_ENV` | **yes** | Must be `production`. **Verify this is actually set** -- its absence is a confirmed finding (see `PENTEST_REPORT_2026-09-17.md` #1): without it, `/docs`/`/openapi.json`/`/redoc` are served publicly and `Strict-Transport-Security` is never sent. |
| `DATABASE_URL` | yes | Points at the local Postgres on the same host (not `localhost` in the sense the `_no_localhost_database_in_production` guard checks for -- confirm this still resolves correctly for wherever Postgres actually runs). |
| `JWT_SECRET_KEY` | yes | `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `CORS_ORIGINS` | yes | Must include the real Vercel frontend origin (`https://stock-tracker-frontend-jade.vercel.app`). |
| `TRUSTED_PROXY_IPS` | no (default `127.0.0.1,::1`) | Only change this if the app is ever reachable through an additional proxy hop besides Tailscale's local one -- see `app/rate_limit.py`. |
| `GROQ_API_KEY`, `ALERT_WEBHOOK_URL`, `BACKUP_S3_*` | no | Same meaning as in `DEPLOYMENT.render.md` -- optional features, no-ops when unset. |

## Deploying an update

There is no CI/CD and no git on the server, so a deploy is a manual file
copy + migrate + restart. This is the process used so far (see also
`PENTEST_REPORT_2026-09-17.md` remediation for why item #2 there needed
exactly this):

**1. From your machine, package only the backend files that changed:**

```bash
tar -czf backend_update.tar.gz app alembic/versions/<new_migration_file>.py
```

(Include `pyproject.toml`/`uv.lock` too if dependencies actually changed --
check with `git diff --stat <prev-deployed-commit> HEAD -- pyproject.toml uv.lock`
first; if they didn't, skip re-syncing `.venv` entirely.)

**2. Copy it to the server and extract:**

```bash
scp -i ~/.ssh/fedora_tablet backend_update.tar.gz mrvick@192.168.1.7:/tmp/
ssh -i ~/.ssh/fedora_tablet mrvick@192.168.1.7 "tar -xzf /tmp/backend_update.tar.gz -C /opt/stock-tracker && rm /tmp/backend_update.tar.gz"
```

**3. On the server, migrate and restart:**

```bash
cd /opt/stock-tracker
.venv/bin/python -m alembic upgrade head
sudo systemctl restart stock-api.service
journalctl -u stock-api.service -n 30 --no-pager
```

**4. Verify:**

```bash
curl -sS -o /dev/null -w "status=%{http_code}\n" https://<funnel-host>/docs   # expect 404 if APP_ENV=production
curl -sS -D - -o /dev/null https://<funnel-host>/api/status                  # expect Strict-Transport-Security header
```

**Frontend** deploys separately and automatically: pushing to `main` on
GitHub triggers a Vercel rebuild. Confirm on the Vercel dashboard that the
latest deployment matches the commit you expect.

## Rolling back

**There is no automatic rollback.** Unlike the Render design (which never
cuts traffic to a build that fails its migration/health check), a bad deploy
here means gunicorn is now running new code against whatever schema state
`alembic upgrade head` left behind.

- **Code rollback:** re-run the deploy steps above with a tarball built from
  the previous known-good commit.
- **Schema rollback:** `.venv/bin/python -m alembic downgrade <previous-revision>`
  -- only safe if the migration you're undoing has a working `downgrade()`
  (all migrations in this repo do, by convention) and no data written under
  the new schema needs to survive.
- Take a backup (`python -m app.jobs.backup_db`, if `BACKUP_S3_*` is
  configured) before any deploy that includes a migration, same as you would
  before any other production schema change.

## Known gaps (tracked, not yet fixed)

- **gunicorn binds `0.0.0.0:8000`**, not `127.0.0.1:8000` -- it's reachable
  directly on the home LAN, not just through Tailscale Funnel. Not currently
  known to be exploited, but it's a wider attack surface than necessary if
  Tailscale is meant to be the only path in. Consider binding to loopback
  only and letting `tailscale serve` be the sole entry point, if LAN access
  isn't actually needed.
- **No systemd timer for `app/jobs/backup_db`** exists in `deploy/systemd/`
  (only `stock-daily-pipeline.timer` and `stock-healthcheck.timer` do) --
  confirm whether backups are actually scheduled on this host at all, or
  only ever run manually. If unscheduled, that's the single biggest
  data-loss risk in this deployment.
- **`stock-api.service`'s unit file isn't checked into this repo.** Whatever
  is installed at `/etc/systemd/system/stock-api.service` on the Fedora box
  is the only copy. Recommend copying it into `deploy/systemd/stock-api.service`
  (with paths/secrets templated out, same convention as the other two units)
  so a lost or reimaged host isn't a from-scratch reconstruction.

## Scheduling & alerting

Same jobs, same cadence as documented in [README.md](README.md)
("Scheduling" / "Alerting") -- `stock-daily-pipeline.timer` (20:30 IST,
Mon-Fri) and `stock-healthcheck.timer` (hourly), installed per README's
"Install" steps. Confirm they're actually enabled on this host with:

```bash
systemctl list-timers 'stock-*'
```

## Creating users

No public sign-up (see `CLAUDE.md` -- "no multi-tenancy, public sign-up").
Create accounts with the job script, run on the server inside the venv:

```bash
cd /opt/stock-tracker
.venv/bin/python -m app.jobs.create_user --email "user@example.com" --name "Full Name"
# prompts for the password interactively -- never pass it as an argument (shell history/ps)
```
