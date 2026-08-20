# Production image for the API (app.main:app) and its jobs (app/jobs/*,
# invoked with a different command by the cron services -- see render.yaml).
# One image, two use cases: avoids maintaining a second Dockerfile that can
# drift from this one.

# ---- builder: resolve dependencies into a throwaway prefix ----
FROM python:3.11-slim AS builder
WORKDIR /build

COPY pyproject.toml ./
COPY app ./app
# Base image's bundled pip has known advisories (pip-audit); it's a
# build-time tool only -- nothing in the running app shells out to pip --
# but bumping it is free and one less thing to explain away in a scan.
RUN pip install --no-cache-dir --upgrade pip
# psycopg2-binary ships a self-contained wheel (bundled libpq) -- no
# compiler or libpq-dev needed to build this. `pip install .` reads only
# [project.dependencies], never the "dev" extra (pytest/ruff) -- that's
# what keeps those out of the final image.
RUN pip install --no-cache-dir --prefix=/install .

# ---- runtime ----
FROM python:3.11-slim
WORKDIR /app

# postgresql-client-16, pinned to match our Postgres server's major version
# EXACTLY (docker-compose.yml, render.yaml's postgresMajorVersion) -- not
# the generic "postgresql-client" meta-package, which tracks whatever major
# version is current in Debian's own repo (17 as of this base image) and
# not the server's. Confirmed the hard way during backup verification: a
# dump taken with pg_dump 17 against a PG16 server embeds a `SET
# transaction_timeout = 0` the PG16 target doesn't recognize, and
# pg_restore fails to apply it. The official PGDG apt repo is the only
# source for an exact-matching postgresql-client-16 on this base image.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/keyrings/pgdg.asc \
  && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
  && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
  && apt-get purge -y curl gnupg && apt-get autoremove -y --purge \
  && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/pgdg.list

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY --from=builder /install /usr/local
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./

RUN chmod +x entrypoint.sh && chown -R appuser:appgroup /app
USER appuser

# HOME: the system user has no home directory by default, which makes
# gunicorn's control server fail (harmlessly, but noisily) trying to write
# to /nonexistent -- point it at the one directory this user actually owns.
ENV HOME=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# No curl in slim -- a one-line urllib request is enough and skips an extra
# apt-get layer. Hits the unauthenticated liveness endpoint (app/api/health.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# CMD, not ENTRYPOINT: the cron services in render.yaml override this
# entirely with their own dockerCommand (daily_pipeline / healthcheck) --
# they don't want the migrate+serve wrapper, just the job itself.
CMD ["./entrypoint.sh"]
