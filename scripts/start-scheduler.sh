#!/usr/bin/env sh
set -eu

project_slug="${PROJECT_SLUG:-worker_template}"

exec uv run taskiq scheduler "${project_slug}.scheduler:scheduler"
