#!/bin/sh
# Web service entrypoint: migrate, then serve. `set -e` means a failed
# migration exits this script (and the container) before gunicorn ever
# starts -- Render's health check on the new instance then never passes, so
# it never receives traffic and the previous (still-consistent) instance
# keeps serving. See DEPLOYMENT.md "Migrations".
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting API server..."
# WEB_CONCURRENCY=2: this app never calls out to a third party mid-request
# (CLAUDE.md -- reads only from our own DB), so every request is a bounded
# SQL round-trip, not a long block. 2 workers is enough headroom on a small
# instance (Render's Starter plan is 0.5 CPU / 512MB) to keep one slow
# request from head-of-line blocking everyone else, and to keep serving
# during a worker restart. Bump it if you size up the instance or see
# request queueing under load -- (2 x CPU) + 1 is the usual ceiling.
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout 60 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
