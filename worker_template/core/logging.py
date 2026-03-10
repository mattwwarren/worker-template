"""Structured logging with task context for async worker operations.

This module provides context-aware logging using Python's contextvars to maintain
task context across async operations. Worker tasks automatically include task_id,
tenant_id, and task_name in all log entries.

Usage:
    import logging
    from worker_template.core.logging import get_logging_context, set_task_context

    logger = logging.getLogger(__name__)

    async def process_document(task_id, tenant_id):
        set_task_context(task_id=str(task_id), task_name="process_document", tenant_id=str(tenant_id))
        context = get_logging_context()
        logger.info("processing_document", extra=context)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

LOGGER = logging.getLogger(__name__)

# ContextVars for task-scoped logging context
_task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
_tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_task_name_var: ContextVar[str | None] = ContextVar("task_name", default=None)


def set_task_context(
    *,
    task_id: str | None = None,
    tenant_id: str | None = None,
    task_name: str | None = None,
) -> None:
    """Set task context for current async task.

    Args:
        task_id: Unique identifier for the task execution
        tenant_id: Tenant/organization ID for multi-tenant isolation
        task_name: Human-readable task name for logging
    """
    if task_id is not None:
        _task_id_var.set(task_id)
    if tenant_id is not None:
        _tenant_id_var.set(tenant_id)
    if task_name is not None:
        _task_name_var.set(task_name)


def clear_task_context() -> None:
    """Clear all task context vars."""
    _task_id_var.set(None)
    _tenant_id_var.set(None)
    _task_name_var.set(None)


def get_task_id() -> str | None:
    """Get task ID from context."""
    return _task_id_var.get()


def get_tenant_id() -> str | None:
    """Get tenant ID from context."""
    return _tenant_id_var.get()


def get_task_name() -> str | None:
    """Get task name from context."""
    return _task_name_var.get()


def get_logging_context() -> dict[str, str | None]:
    """Get all logging context as dict for structured logging.

    Returns:
        Dict with task_id, tenant_id, task_name (values may be None)
    """
    return {
        "task_id": get_task_id(),
        "tenant_id": get_tenant_id(),
        "task_name": get_task_name(),
    }


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log message with automatic task context injection.

    Args:
        logger: Logger instance to use
        level: Logging level (logging.INFO, logging.WARNING, etc.)
        message: Log message
        extra: Additional fields to include in log entry
    """
    merged_extra = get_logging_context()
    if extra:
        merged_extra.update(extra)
    logger.log(level, message, extra=merged_extra)


def get_logger(name: str) -> logging.Logger:
    """Get logger instance with module name.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
