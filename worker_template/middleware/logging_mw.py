"""Logging middleware for task execution context."""

from __future__ import annotations

import logging
from typing import Any

from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from worker_template.core.logging import clear_task_context, get_logging_context, set_task_context

LOGGER = logging.getLogger(__name__)


class LoggingMiddleware(TaskiqMiddleware):
    """Set logging context and log task lifecycle events."""

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Set task logging context and log task start."""
        set_task_context(
            task_id=message.task_id,
            task_name=message.task_name,
        )
        context = get_logging_context()
        LOGGER.info(
            "task_started",
            extra={
                **context,
                "task_id": message.task_id,
                "task_name": message.task_name,
            },
        )
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Log task completion."""
        context = get_logging_context()
        if result.is_err:
            LOGGER.error(
                "task_error",
                extra={
                    **context,
                    "task_id": message.task_id,
                    "task_name": message.task_name,
                    "error": str(result.error),
                },
            )
        else:
            LOGGER.info(
                "task_completed",
                extra={
                    **context,
                    "task_id": message.task_id,
                    "task_name": message.task_name,
                },
            )

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        """Log task exception."""
        context = get_logging_context()
        LOGGER.exception(
            "task_exception",
            extra={
                **context,
                "task_id": message.task_id,
                "task_name": message.task_name,
                "exception_type": type(exception).__name__,
            },
        )
        clear_task_context()
