"""Prometheus metrics for task execution monitoring.

Business Domain Metrics:
    This module defines metrics for worker-specific operations:
    task execution counts, durations, queue depth, and error rates.

Usage:
    from worker_template.core.metrics import tasks_completed_total
    from worker_template.core.config import settings

    tasks_completed_total.labels(
        environment=settings.environment,
        task_name="process_document",
    ).inc()
"""

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

# Task lifecycle metrics
tasks_started_total = Counter(
    "tasks_started_total",
    "Total number of tasks started",
    ["environment", "task_name"],
)

tasks_completed_total = Counter(
    "tasks_completed_total",
    "Total number of tasks completed successfully",
    ["environment", "task_name"],
)

tasks_failed_total = Counter(
    "tasks_failed_total",
    "Total number of tasks that failed",
    ["environment", "task_name"],
)

tasks_retried_total = Counter(
    "tasks_retried_total",
    "Total number of task retry attempts",
    ["environment", "task_name"],
)

# Duration tracking
task_duration_seconds = Histogram(
    "task_duration_seconds",
    "Task execution duration in seconds",
    ["task_name"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0, 1800.0, 3600.0],
)

# Worker state
tasks_in_progress = Gauge(
    "tasks_in_progress",
    "Number of tasks currently being executed",
    ["task_name"],
)

# Database query performance
database_query_duration_seconds = Histogram(
    "database_query_duration_seconds",
    "Database query duration in seconds",
    ["query_type"],
    buckets=[0.001, 0.01, 0.05, 0.1, 0.5, 1.0],
)

metrics_app = make_asgi_app()
