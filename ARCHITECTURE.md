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
`description` (identity), `port` (health server), `enable_scheduler`
(scheduler deployment).

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
client code:  task.kiq(raw_input=input.model_dump())
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
4. **StateTrackingMiddleware** — records `TaskExecution` state transitions.
   The full `TaskStatus` enum:

```
PENDING → QUEUED → RUNNING → COMPLETED / FAILED / PARTIAL
                  ↳ RETRYING → RUNNING (re-enqueued; see below)
                  ↳ CANCELLED
```

Of these, the middleware itself sets only `RUNNING` (pre_execute),
`COMPLETED`/`FAILED` (post_execute), and `RETRYING`/`FAILED` (on_error).
Creating the row in the first place (`create_task_execution`, which is what
produces `PENDING`) is the dispatching caller's responsibility, shown by
`dispatch_example_task` in `tasks/example_task.py`.
`QUEUED`/`CANCELLED`/`PARTIAL` remain states an instance sets itself; the
template ships no code path for them.

`on_error` re-enqueues for real: when `task.retry_count < task.max_retries`
and the tenant/task retry gate is enabled, it writes `RETRYING`, then re-kicks
the same message (same `task_id`, same args/kwargs/labels) via
`taskiq.kicker.AsyncKicker`, immediately — no delay/backoff, since the shipped
`AioPikaBroker` has no delay queue configured. The gate is disabled by default
and requires both non-empty, matching tenant and task allowlists; an empty
allowlist denies automatic retries. Shadow mode records intended retries as
nonterminal `RETRYING` rows without dispatching them, leaving an explicit
operator recovery point. Every retry decision and dispatch outcome is also
written to the `task_attempt` audit table. If the re-kick itself
fails to send, the row is reconciled to `FAILED` ("Task failed (retry dispatch
error)") in the same `on_error` call. On successful dispatch, `on_error` marks
`result.error` with TaskIQ's `NoResultError` sentinel; `post_execute` checks
for that sentinel first and returns immediately, so it never clobbers the
`RETRYING` (or, for a synchronous retry chain such as the `InMemoryBroker`
under `await_inplace=True`, an already-`COMPLETED`) status the retried
attempt's own `on_error`/`post_execute` just wrote.

(This section is the authoritative description of the state machine and
middleware pipeline; other docs point here.)

Two transaction boundaries exist by design, and they are independent:

- **The task body owns its domain transaction.** Services flush; the task
  commits (same rule as the API template's "endpoints commit").
- **StateTrackingMiddleware owns the TaskExecution row's transaction**, in
  its own session, committed in every branch, with `@db_retry` retrying the
  commit on transient `OperationalError` — so the recorded RUNNING/FAILED
  status survives even when the task's own transaction rolls back.

The dispatching caller (`dispatch_example_task`) is a third committer outside
the task body and the middleware: it commits the `TaskExecution` row in its
own session before enqueueing, so the middleware's separate session can read it.

State transitions also emit realtime events (fire-and-forget) — see below.

> **Known gaps:** (1) *Resolved* — `on_error` uses a tenant/task-scoped,
> opt-in retry gate requiring both matching allowlists (with nonterminal shadow
> mode) and re-enqueues via `AsyncKicker` when `task.retry_count <
> task.max_retries`; each decision and dispatch outcome is recorded in
> `task_attempt`; see above. (2) There is no idempotency-key pattern;
> `parent_task_id` supports task trees, not dedup. (3) The example task now
> has a demonstrated dispatch path: `dispatch_example_task` creates the
> `PENDING` row and threads `task_execution_id` through keyword-form
> `raw_input`, so state tracking and realtime events fire end-to-end. The row
> is committed before the enqueue, so a failed or lost enqueue leaves an
> orphaned `PENDING` row. There is no outbox, recovery, or reconciliation for
> that case; it is accepted as non-blocking and recorded here rather than
> solved. Treat (2) and (3) as instance-level decisions, not shipped
> behavior.

## Data layer

Shared skeleton with fastapi-template, near line-for-line:

- `TimestampedTable` (`models/base.py`) — server-generated UUID PKs
  (`gen_random_uuid()`), timezone-aware DB-managed timestamps.
- `db/session.py` — same `PoolConfig` / `create_db_engine` /
  `create_session_maker` factories and module-level singletons. The
  middleware (`state_tracking.py`) and `dispatch_example_task` look up
  `db_session.async_session_maker` as a module attribute at call time, so
  test fixtures can rebind it; the `get_session()` generator exists for
  parity but has no production call site here. See §7 P1 for why psycopg
  is the single driver, sync and async alike.
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

- `TASKIQ_ENV=test` is set at the top of `worker_template/tests/conftest.py`
  before any import, so the broker singleton materializes as
  `InMemoryBroker` — no RabbitMQ/Redis needed in tests.
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
- CI (`ci.yml`) runs on every PR to `main` and every push to `main`:
  Pre-commit checks, Lint (`ruff check` + `ruff format --check`), Type Check
  (`mypy worker_template`), Unit Tests, Integration Tests (real Postgres via
  `pytest-docker`), and a Coverage Check that combines both suites' coverage
  and enforces `--fail-under=90`. `validate-template.yml` templatizes the
  repo, generates a project via Copier for each `enable_scheduler` matrix
  leg, and validates the generated output's own pre-commit/lint/mypy/Docker
  build — see the generated-output drift principle (§7 P4). Together these
  are the 7 required status checks branch protection enforces on `main`
  (Pre-commit checks, Lint, Type Check, Unit Tests, Integration Tests,
  Coverage Check, Validate (default)); merging is via `/ship-it`, which arms
  `gh pr merge --auto` once they're required (see
  `.claude/commands/ship-it.md`). Dependency version ceilings follow the
  same pattern — see §7 P3.

## Invariants (the short list)

The fuller rationale behind these — the why, not just the what — lives in
§7/§8 below; this list stays terse on purpose.

1. Async-only, end to end.
2. Tasks commit their own domain writes; services flush;
   StateTrackingMiddleware commits status in its own session.
3. Task I/O is dict-at-the-boundary, Pydantic-validated inside the task
   (`tasks/contracts.py`).
4. Every table extends `TimestampedTable`; every model is imported in
   `db/base.py`; schema changes go through Alembic autogenerate. *(see §7 P2,
   §8 A1)*
5. Tenancy travels in the task contract (`tenant_id` kwarg → ContextVar),
   never ambiently.
6. Realtime is write-only from the worker, room-scoped per tenant, and can
   never fail a task.
7. Task registration is import-driven — new task modules must be imported in
   `tasks/__init__.py`.
8. The broker is env-switched: tests always run on `InMemoryBroker`.

## 7. Principles

### P1. psycopg is the only Postgres driver, sync and async alike

`core/config.py`'s `database_url` default and every production session
factory in `db/session.py` speak `postgresql+psycopg://`, and
`worker_template/tests/conftest.py`'s synchronous DB-creation helper and
Alembic sync engine use the same `psycopg` DBAPI, just in synchronous mode.
Standardizing on one driver means every code path — async application
traffic, sync test bootstrapping, sync Alembic migrations — shares one
DBAPI's connection semantics, pooling behavior, and error types, instead of
the previous asyncpg/psycopg split forcing two drivers' exception hierarchies
and transaction-sharing quirks to be reasoned about side by side (a
sync/async transaction-sharing test pitfall the split otherwise invites).
`psycopg[binary]` is a runtime dependency (`pyproject.toml`), which ships the
precompiled C extension; production images should track upstream guidance on
`psycopg[c]` vs `psycopg[binary]` if that tradeoff needs revisiting.

### P2. Primitives are refreshed via CLI, never hand-edited

Alembic migration files (`alembic/versions/*.py`) are generated by `alembic
revision --autogenerate` against the current `SQLModel` metadata
(`ARCHITECTURE.md`, "Data layer") and are not edited by hand afterward —
every migration in the repo still carries the unmodified autogenerate
header. Hand-editing a migration silently decouples it from the model
diff it was generated to represent, and the next autogenerate run won't
know to reconcile a change nobody told it about.

### P3. Dependency version ceilings carry an expiry

A holdback ceiling (`[tool.uv].constraint-dependencies` in `pyproject.toml`)
is only added paired with a tracked follow-up ticket to re-verify and lift
it — it is a "not yet" ceiling, never a "not ever" one. PR #5 added six
holdbacks (aio-pika, aiormq, pamqp, redis, mypy, starlette/fastapi) after a
safe-sweep upgrade; PRs #6 and #7 each lifted their share once the breaking
major was vetted. `pyproject.toml` currently has no `[tool.uv]` section at
all — every ceiling that was added has since been resolved, none left to
silently rot into a permanent floor nobody revisits.

### P4. Generated-output drift is CI-gated, not manually policed

`scripts/templatize.sh` (`Step 6/6`, "remaining hardcoded references")
fails the build if any literal `worker_template` string leaks into the
Copier-templatized tree, and `validate-template.yml` runs that guard plus a
full Copier-generate → pre-commit/lint/mypy/Docker-build cycle, on both the
default and `enable_scheduler=false` matrix legs, on every PR. This caught
a real violation once (PR #24 committed `.claude/review-verdict.*` files
containing the string `worker_template`, which the guard rejected) — the
mechanism is load-bearing, not aspirational.

### P5. Never inline the package/slug path in long string literals

Source lines whose width depends on the `worker_template` slug can wrap
differently once `scripts/templatize.sh` substitutes a shorter or longer
`project_slug`, so `ruff format --check` can pass here and fail on the
generated output. Hoist any repeated, slug-bearing dotted path into a
module-level constant instead of inlining it into every f-string —
`worker_template/tests/unit/test_state_tracking_mw.py`'s
`_STATE_TRACKING_SETTINGS` is the shipped example: one constant, reused via
`f"{_STATE_TRACKING_SETTINGS}.task_retry_enabled"` everywhere a settings
attribute needs monkeypatching, instead of ten copies of the full dotted
path at ten different line lengths.

## 8. Anti-patterns

### A1. Hand-editing a generated Alembic migration

Editing an already-generated file under `alembic/versions/` instead of
changing the `SQLModel` table and re-running `alembic revision
--autogenerate` breaks the invariant that migrations are an exact diff of
model state (Invariant 4; §7 P2) — the file stops matching what autogenerate
would produce from the current models, and the next autogenerate run has no
way to detect or reconcile the manual edit.

### A2. Floating Docker image tags in deployment or test manifests

`k8s/` manifests and `tests/docker-compose.yml` pin Postgres/RabbitMQ/Redis
to immutable point releases (e.g. `postgres:18.6-alpine`, not
`postgres:18-alpine`) after PR #26 found that a floating major-version tag
lets an upstream image publish silently skew the version running in tests
from the version running in k8s, with no error until behavior actually
diverges. Any new image reference in either location must be pinned the
same way.

### A3. Committing ephemeral session/review-artifact output

Files like `.claude/review-verdict.*` are cw/auto-dev session output, not
source, and must not be committed: besides being noise, they fail
pre-commit's end-of-file fixer (written without a trailing newline) and,
because their content contains the literal string `worker_template`, trip
`scripts/templatize.sh`'s remaining-references guard (§7 P4) on unrelated
PRs. PR #24 hit this exact failure and had to add an explicit exclusion
pattern after the fact — new ephemeral output should be `.gitignore`d up
front instead.
