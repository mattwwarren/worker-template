FROM ghcr.io/astral-sh/uv:alpine

ENV PYTHONUNBUFFERED=1 \
    UV_NO_DEV=1 \
    UV_PYTHON_INSTALL_DIR=/app/.python

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked -n --no-progress --no-install-project
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY worker_template ./worker_template/
RUN uv sync --locked -n --no-progress

RUN addgroup -g 1000 -S app && adduser -u 1000 -S app -G app \
    && chown -R app:app /app

USER app

EXPOSE 8080

CMD ["sh", "scripts/start.sh"]
