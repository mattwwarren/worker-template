#!/usr/bin/env sh
set -eu

health_port="${HEALTH_SERVER_PORT:-8080}"
concurrency="${WORKER_CONCURRENCY:-10}"
project_slug="${PROJECT_SLUG:-worker_template}"

# Run database migrations
uv run alembic upgrade head

# Start health server in background
uv run uvicorn "${project_slug}.health_server:health_app" \
  --host 0.0.0.0 \
  --port "${health_port}" &

# Start TaskIQ worker in foreground
exec uv run taskiq worker "${project_slug}.worker:broker" \
  --workers "${concurrency}"
