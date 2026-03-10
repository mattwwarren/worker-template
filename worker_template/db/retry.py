"""Database retry decorators for handling transient failures.

Usage:
    from worker_template.db.retry import db_retry

    @db_retry
    async def create_record(session: AsyncSession, record: Model) -> Model:
        session.add(record)
        await session.commit()
        return record
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from sqlalchemy.exc import OperationalError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from worker_template.core.logging import get_logging_context

LOGGER = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_WAIT_MULTIPLIER = 1
DEFAULT_MIN_WAIT = 1
DEFAULT_MAX_WAIT = 10


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log retry attempts with structured context."""
    base_context = get_logging_context()
    wait_seconds = getattr(retry_state.next_action, "sleep", 0) if retry_state.next_action else 0
    exception_type = type(retry_state.outcome.exception()).__name__ if retry_state.outcome else "unknown"

    extra = {
        **base_context,
        "attempt": retry_state.attempt_number,
        "wait_seconds": wait_seconds,
        "exception_type": exception_type,
    }
    LOGGER.warning("db_operation_retry", extra=extra)


def create_db_retry(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    wait_multiplier: int = DEFAULT_WAIT_MULTIPLIER,
    min_wait: int = DEFAULT_MIN_WAIT,
    max_wait: int = DEFAULT_MAX_WAIT,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Create a database retry decorator with custom configuration.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        wait_multiplier: Multiplier for exponential backoff (default: 1)
        min_wait: Minimum wait time in seconds (default: 1)
        max_wait: Maximum wait time in seconds (default: 10)

    Returns:
        Configured retry decorator
    """
    return retry(
        retry=retry_if_exception_type(OperationalError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=wait_multiplier, min=min_wait, max=max_wait),
        before_sleep=_log_retry_attempt,
        reraise=True,
    )


db_retry: Callable[[Callable[P, T]], Callable[P, T]] = create_db_retry()
