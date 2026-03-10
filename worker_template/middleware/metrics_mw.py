"""Prometheus metrics middleware for task execution."""

from __future__ import annotations

import time
from typing import Any

from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from worker_template.core.config import settings
from worker_template.core.metrics import (
    task_duration_seconds,
    tasks_completed_total,
    tasks_failed_total,
    tasks_in_progress,
    tasks_started_total,
)

_TASK_START_TIME_KEY = "_metrics_start_time"


class MetricsMiddleware(TaskiqMiddleware):
    """Record Prometheus metrics for task execution."""

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Record task start and increment in-progress gauge."""
        tasks_started_total.labels(
            environment=settings.environment,
            task_name=message.task_name,
        ).inc()
        tasks_in_progress.labels(task_name=message.task_name).inc()

        # Store start time in message labels for duration calculation
        message.labels[_TASK_START_TIME_KEY] = str(time.monotonic())
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Record task completion metrics."""
        tasks_in_progress.labels(task_name=message.task_name).dec()

        # Record duration
        start_time_str = message.labels.get(_TASK_START_TIME_KEY)
        if start_time_str is not None:
            duration = time.monotonic() - float(start_time_str)
            task_duration_seconds.labels(task_name=message.task_name).observe(duration)

        if result.is_err:
            tasks_failed_total.labels(
                environment=settings.environment,
                task_name=message.task_name,
            ).inc()
        else:
            tasks_completed_total.labels(
                environment=settings.environment,
                task_name=message.task_name,
            ).inc()

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        """Record task failure metrics."""
        tasks_in_progress.labels(task_name=message.task_name).dec()
        tasks_failed_total.labels(
            environment=settings.environment,
            task_name=message.task_name,
        ).inc()

        # Record duration even on error
        start_time_str = message.labels.get(_TASK_START_TIME_KEY)
        if start_time_str is not None:
            duration = time.monotonic() - float(start_time_str)
            task_duration_seconds.labels(task_name=message.task_name).observe(duration)
