# Architecture

This document describes how the worker template is put together: the process
model, the task pipeline, the layering rules, and the invariants that keep
generated projects consistent. It mirrors the section structure of
`fastapi-template/ARCHITECTURE.md` — the two templates share a skeleton and
are designed to run side by side against the same Postgres and Redis.

## The template model: runnable-first

The repository is **directly runnable Python** on `main`. There is no Jinja
templating in the source; `scripts/templatize.sh` performs a literal
`worker_template` → `{{ project_slug }}` substitution at release time to
produce the Copier template. Production instances are generated with
`copier copy` and updated with `copier update`.

Copier variables (`copier.yaml`): `project_name` / `project_slug` /
`description` (identity), `port` (health server), `multi_tenant`,
`enable_metrics`, `enable_scheduler`, `database_enabled`.

> **Known gap:** `multi_tenant`, `enable_scheduler`, and `database_enabled`
> are collected at generation time but are not wired to any conditional
> generation logic — every generated project currently ships tenant
> isolation, the scheduler deployment, and TaskExecution tracking regardless
> of the answers. Only metrics has a working toggle, and it is the
> **runtime** env var `ENABLE_METRICS` (gates mounting `/metrics` on the
> health server), not the Copier variable.

## Process model

One codebase, three processes:

1. **Worker** — `taskiq worker worker_template.worker:broker`
   (`scripts/start.sh`; runs `alembic upgrade head` first, then backgrounds
   the health server and execs the worker as PID 1).
2. **Scheduler** — `taskiq scheduler worker_template.scheduler:scheduler`
   (`scripts/start-scheduler.sh`), a separate deployment. Cron schedules are
   declared as labels on the task itself
   (`@broker.task(schedule=[{"cron": "0 */6 * * *"}])`) via
   `LabelScheduleSource` — there is no separate schedule config file.
3. **Health server** — a lightweight FastAPI app (`health_server.py`) run by
   uvicorn on its own port inside the worker container, serving `/health`,
   `/ready`, and (when `ENABLE_METRICS` is set) `/metrics`. `/ready` is
   currently a stub — it reports ready whenever the process is up, without
   checking broker/DB connectivity.

The **broker singleton** (`broker.py`) is the seam between them all:
`TASKIQ_ENV=test` → `InMemoryBroker` (no external services); otherwise
`AioPikaBroker` (RabbitMQ transport) with `RedisAsyncResultBackend` (Redis
holds task results only — the queue itself is RabbitMQ).

Worker startup (`@broker.on_event("startup")` in `worker.py`) validates
config, creates the async DB engine/session maker onto TaskIQ state, and
initializes the realtime emitter when `REDIS_URL` is set; shutdown disposes
both. Task modules are imported in `worker.py` / `tasks/__init__.py` purely
for their `@broker.task` registration side effect — a task module that isn't
imported there does not exist as far as the broker is concerned.

## Layering

```
client code:  task.kiq(input.model_dump())
    │
    ▼  RabbitMQ (AioPika)
Middleware pipeline (logging → tenant → metrics → state tracking)
    │
    ▼
tasks/        task bodies — validate input via Pydantic contract,
    │         orchestrate, commit their own domain writes
    ▼
services/     business/data helpers — session-first args, flush not commit
    │
    ▼
models/       SQLModel tables (TimestampedTable: UUID PK, DB timestamps)
    │
    ▼
db/           async engine/session factories + Alembic model registry
    │
    ▼
Postgres  (+ Redis for results/realtime, RabbitMQ for transport)
```

Task I/O crosses the broker boundary as plain dicts; typing lives in
`tasks/contracts.py` (`TaskInput`/`TaskOutput` Pydantic bases). The task
body's first act is `Model.model_validate(raw_input)`; its last is
`output.model_dump()`.

### Directory map

- `worker_template/broker.py` — broker singleton (env-switched)
- `worker_template/worker.py` / `scheduler.py` / `health_server.py` —
  process entrypoints
- `worker_template/middleware/` — TaskIQ middleware pipeline (see below)
- `worker_template/tasks/` — task contracts + registered task bodies
- `worker_template/services/` — data access helpers (flush, never commit)
- `worker_template/models/` — SQLModel tables; `task_execution.py` is the
  state-machine row
- `worker_template/core/` — config, ContextVar logging, Prometheus metrics,
  tenant ContextVar
- `worker_template/db/` — same engine/session/PoolConfig skeleton as
  fastapi-template (minus the FastAPI `SessionDep` wrapper), plus
  `retry.py`
- `worker_template/realtime/` — write-only Socket.IO emitter
- `alembic/`, `k8s/`, `devspace.yaml`, `Dockerfile`, `scripts/` —
  migrations and deployment surface

## Task lifecycle and the middleware pipeline

"Middleware" here is TaskIQ's `TaskiqMiddleware` hook system — it wraps
**task execution** the way HTTP middleware wraps requests. Registration
order is fixed in `middleware/__init__.py`; `pre_execute` runs
top-to-bottom, `post_execute`/`on_error` bottom-to-top:

1. **LoggingMiddleware** — sets/clears task ContextVars (`task_id`,
   `task_name`), logs `task_started` / `task_completed` / `task_error`.
2. **TenantMiddleware** — extracts `tenant_id` from task kwargs (directly or
   nested in `raw_input`) into a ContextVar. This is the worker's tenancy
   model: no HTTP middleware, tenant comes in through the task contract.
3. **MetricsMiddleware** — Prometheus counters/histogram/gauge around every
   task (`tasks_started_total`, `task_duration_seconds`,
   `tasks_in_progress`, …).
4. **StateTrackingMiddleware** — drives the `TaskExecution` state machine:

```
PENDING → QUEUED → RUNNING → COMPLETED/FAILED
                  ↳ RETRYING (label) ↳ CANCELLED
```

Two transaction boundaries exist by design, and they are independent:

- **The task body owns its domain transaction.** Services flush; the task
  commits (same rule as the API template's "endpoints commit").
- **StateTrackingMiddleware owns the TaskExecution row's transaction**, in
  its own session, committed in every branch — so the recorded
  RUNNING/FAILED status survives even when the task's own transaction rolls
  back.

State transitions also emit realtime events (fire-and-forget) — see below.

> **Known gaps:** (1) `RETRYING` is a status label — the middleware
> increments `retry_count` and records the state, but nothing re-enqueues
> the message; TaskIQ does not retry automatically and no retry middleware
> is wired. (2) `db/retry.py` (`@db_retry`, tenacity backoff for transient
> `OperationalError`) exists and is tested but is not applied to any
> production call site. (3) There is no idempotency-key pattern;
> `parent_task_id` supports task trees, not dedup. Treat all three as
> instance-level decisions, not shipped behavior.

## Data layer

Shared skeleton with fastapi-template, near line-for-line:

- `TimestampedTable` (`models/base.py`) — server-generated UUID PKs
  (`gen_random_uuid()`), timezone-aware DB-managed timestamps.
- `db/session.py` — same `PoolConfig` / `create_db_engine` /
  `create_session_maker` factories and module-level singletons. Production
  code paths (worker startup, middleware) use `async_session_maker()`
  directly; the `get_session()` generator exists for parity but has no
  production call site here.
- Migrations are ORM-exclusive via Alembic autogenerate; `db/base.py` must
  import every model so `SQLModel.metadata` is complete.
- `core/config.py` — same pydantic-settings pattern, extended with
  `rabbitmq_*`, `redis_url`, `worker_concurrency`, `health_server_port`,
  `task_default_timeout_seconds`, `task_max_retries`,
  `enforce_tenant_isolation`.

## Realtime (write-only)

`realtime/emitter.py` builds a Socket.IO `AsyncServer` over a
**write-only** `AsyncRedisManager` — the worker never accepts connections;
it publishes through the same Redis pub/sub the FastAPI service's Socket.IO
server reads, so emits reach clients connected to the API. Events go to the
room `org:{tenant_id}`, are fire-and-forget (a failed emit can never crash a
task), and are emitted only by `StateTrackingMiddleware` — realtime events
are 1:1 with state-machine transitions.

`realtime/contracts.py` deliberately **duplicates** the FastAPI template's
event models (no shared package); the OpenAPI spec exported by the API
service is the source of truth for clients, and integration tests are the
guard that keeps the worker's copies in sync.

## Observability

- ECS-style structured logging keyed on task context (`task_id`,
  `task_name`, `tenant_id`) via ContextVars — the worker analogue of the API
  template's request-context logging.
- Prometheus task-lifecycle metrics exposed on the health server's
  `/metrics` (runtime-gated by `ENABLE_METRICS`).

## Testing architecture

- `TASKIQ_ENV=test` is set at the top of `tests/conftest.py` before any
  import, so the broker singleton materializes as `InMemoryBroker` — no
  RabbitMQ/Redis needed in tests.
- `pytest-docker` starts Postgres from the repo-root
  `tests/docker-compose.yml`; migrations run via real Alembic; a filelock
  refcount makes container setup/teardown xdist-safe, with one database per
  xdist worker.
- `worker_template/tests/unit/` never touches the DB (its conftest no-ops
  the `reset_db` fixture); `tests/integration/` runs against the real
  Postgres, marked `@pytest.mark.integration`.

## Deployment

- Non-root Docker image (uv-based alpine, two-stage dependency caching);
  worker container runs migrations, then the health server and the TaskIQ
  worker; the scheduler is a separate lighter deployment with no probes.
- DevSpace + k3d for local dev: deploys postgres, rabbitmq, redis, worker,
  scheduler standalone, or just worker+scheduler when running as a
  dependency of the meta-workspace (shared infra assumed).
- The `k8s/` manifests (postgres/rabbitmq/redis) are for the template repo's
  own dev loop only — `.copierignore` excludes them from generated projects;
  instance infrastructure is managed at workspace level.
- **No CI in this repo** — acceptance is local gates only
  (`uv run ruff check`, `uv run mypy`, `uv run pytest`); merging is
  operator-owned (see `.claude/commands/ship-it.md`).

## Invariants (the short list)

1. Async-only, end to end.
2. Tasks commit their own domain writes; services flush;
   StateTrackingMiddleware commits status in its own session.
3. Task I/O is dict-at-the-boundary, Pydantic-validated inside the task
   (`tasks/contracts.py`).
4. Every table extends `TimestampedTable`; every model is imported in
   `db/base.py`; schema changes go through Alembic autogenerate.
5. Tenancy travels in the task contract (`tenant_id` kwarg → ContextVar),
   never ambiently.
6. Realtime is write-only from the worker, room-scoped per tenant, and can
   never fail a task.
7. Task registration is import-driven — new task modules must be imported in
   `tasks/__init__.py`.
8. The broker is env-switched: tests always run on `InMemoryBroker`.
