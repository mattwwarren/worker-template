# Worker Template

A reusable Copier template for TaskIQ async worker services.

## Features

- **TaskIQ** async task processing with RabbitMQ broker
- **Redis** result backend for task results
- **Pydantic** data contracts for type-safe task I/O
- **TaskExecution** state tracking with step-based progress
- **Prometheus** metrics for task monitoring
- **Multi-tenant** isolation via ContextVars and DB filtering
- **Health server** for Kubernetes liveness/readiness probes
- **Scheduler** deployment for cron-based task scheduling

## Quick Start

### As a Template (via Copier)

```bash
copier copy gh:mattwwarren/worker-template --vcs-ref copier ./my-worker
cd my-worker
uv sync
```

### For Development

```bash
cd worker-template
uv sync
TASKIQ_ENV=test uv run pytest
```

### With DevSpace

```bash
devspace run cluster-up
devspace dev
```

## Architecture

### Task Flow

1. Client calls `task.kiq(input.model_dump())` to enqueue
2. Broker delivers message to worker
3. Middleware pipeline: logging -> tenant -> metrics -> state tracking
4. Task validates input via `model_validate()`
5. Task processes work, updates progress
6. Result stored in Redis, state updated in PostgreSQL

### Middleware Pipeline

| Middleware | pre_execute | post_execute | on_error |
|-----------|------------|-------------|---------|
| logging | Set context | Log complete | Log error |
| tenant | Extract tenant_id | Clear context | Clear context |
| metrics | Inc started, in_progress | Record duration | Inc failed |
| state_tracking | Set RUNNING | Set COMPLETED/FAILED | Set RETRYING/FAILED |

## License

This is free and unencumbered software released into the public domain.
