# Worker Template - TaskIQ Async Worker Service

Claude Code configuration for TaskIQ async worker development.

## Tech Stack

- **Language**: Python 3.13+
- **Task Framework**: TaskIQ with AioPikaBroker (RabbitMQ)
- **Result Backend**: Redis (taskiq-redis)
- **Database**: PostgreSQL with SQLAlchemy/SQLModel (async)
- **Health Server**: FastAPI (lightweight, port 8080)
- **Testing**: pytest, pytest-asyncio
- **Linting**: ruff, mypy
- **Container**: Docker, Kubernetes

## Project Structure

```
worker-template/
├── worker_template/       # Main Python package
│   ├── broker.py          # TaskIQ broker singleton
│   ├── worker.py          # Worker entrypoint
│   ├── health_server.py   # Health check HTTP server
│   ├── scheduler.py       # Scheduler entrypoint
│   ├── core/              # Configuration, logging, metrics, tenants
│   ├── db/                # Database session, retry
│   ├── models/            # SQLModel database models
│   ├── services/          # Business logic (pure async functions)
│   ├── tasks/             # Task contracts and implementations
│   │   ├── contracts.py   # Pydantic input/output models
│   │   └── example_task.py
│   └── middleware/         # TaskIQ middleware pipeline
│       ├── logging_mw.py  # Structured logging context
│       ├── tenant.py      # Tenant extraction from task kwargs
│       ├── metrics_mw.py  # Prometheus metrics
│       └── state_tracking.py  # TaskExecution DB updates
├── alembic/               # Database migrations
├── scripts/               # Utility scripts
├── tests/                 # Docker Compose for integration tests
├── pyproject.toml
├── copier.yaml            # Copier template configuration
├── _tasks.py              # Post-generation tasks
└── dotenv.example
```

## Key Patterns

### Task Contract Pattern

Tasks use Pydantic models for type-safe serialization:
- `TaskInput` base: includes tenant_id, priority, parent_task_id
- `TaskOutput` base: includes success, result_url, error_detail
- `model_dump()` to queue, `model_validate()` on worker

### TaskExecution State Machine

```
PENDING -> QUEUED -> RUNNING -> COMPLETED/FAILED/PARTIAL
                   ↳ RETRYING -> RUNNING (loop)
                   ↳ CANCELLED
```

### Middleware Pipeline Order

logging_mw -> tenant -> metrics_mw -> state_tracking

### Service Layer

Pure async functions, session-first parameter, flush not commit.

## Development Workflow

### Runnable-First Architecture

This template uses a **runnable-first** architecture:

- **Main branch** contains runnable Python code (`worker_template/`)
- **`copier` branch** contains the Copier template (auto-generated on release)
- Template variables (`{{ project_slug }}`) are generated at release time via GitHub Actions

```bash
# Development happens directly on the template (use --directory flag, no cd needed)
uv --directory /path/to/worker-template sync
uv --directory /path/to/worker-template run pytest
uv --directory /path/to/worker-template run ruff check .
uv --directory /path/to/worker-template run mypy .
```

**Key benefits:**
- No Copier generation step for development iteration
- Instant feedback loop: edit, test, repeat
- Git worktrees enable parallel feature development
- Template is always in a working state

### When to Use Copier

**Use Copier for production instances only:**

```bash
# Creating a new production project from the template
copier copy gh:mattwwarren/worker-template --vcs-ref copier ./my-worker

# Updating an existing production instance with template changes
cd /path/to/my-worker
copier update
```

**Do NOT use Copier for:**
- Development iteration on the template itself
- Running tests during template development
- Quick prototyping of new features

### Testing Template Generation (Pre-Release)

Before releasing, test that template generation works:

```bash
# Generate templatized version
./scripts/templatize.sh

# Test generation locally
copier copy .templatized/ /tmp/test-worker --trust
cd /tmp/test-worker
uv run pytest
```

## Key Patterns Enforced

### Code Quality

- **Zero linter violations** - `ruff check` must pass
- **Zero type errors** - `mypy` must pass
- **100% test pass rate** - All tests must pass
- **No shortcuts** - Fix root causes, no suppressions

### Database Patterns

- Use async SQLAlchemy session management
- Alembic migrations for schema changes
- Proper transaction boundaries
- Session-first parameter in service functions

### Task Patterns

- Task contracts (Pydantic models) for all task I/O
- Middleware pipeline for cross-cutting concerns
- TaskExecution state tracking for observability
- Retry with exponential backoff via middleware

### Testing Patterns

- AAA pattern (Arrange-Act-Assert)
- Real database data, not mocks (for integration tests)
- Test behavior, not implementation
- TASKIQ_ENV=test for test broker configuration

### Async Patterns

- Parallelize with `asyncio.gather()`
- Avoid N+1 database queries
- Proper async context manager usage
- Session lifecycle management

## Agent Model Selection

- **haiku** - Simple searches, file reads, status checks
- **sonnet** - Default for implementation, reviews
- **opus** - Only when user explicitly requests complex reasoning

## Common Workflows

### Add a New Task

1. Define contracts in `worker_template/tasks/contracts.py`
2. Create task implementation in `worker_template/tasks/`
3. Register task in `worker_template/tasks/__init__.py`
4. Write tests in `worker_template/tests/`
5. Run: `uv run pytest && uv run ruff check . && uv run mypy .`

### Debug a Task

1. Check task state in TaskExecution table
2. Review structured logs (task_id, tenant_id context)
3. Check Prometheus metrics for error rates
4. Write test to reproduce the failure

## License

This is free and unencumbered software released into the public domain.

For more information, please refer to <http://unlicense.org/>
