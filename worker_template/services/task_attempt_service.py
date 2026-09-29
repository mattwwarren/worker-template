"""Data access service for task retry audit records."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from worker_template.models.task_attempt import TaskAttempt, TaskDispatchResult
from worker_template.models.task_execution import TaskStatus


async def record_task_attempt(
    session: AsyncSession,
    *,
    task_execution_id: UUID,
    tenant_id: UUID,
    attempt_number: int,
    status_before: TaskStatus,
    status_after: TaskStatus,
    error_detail: str | None,
    dispatch_result: TaskDispatchResult,
) -> TaskAttempt:
    """Record a durable audit entry for one retry decision."""
    attempt = TaskAttempt(
        task_execution_id=task_execution_id,
        tenant_id=tenant_id,
        attempt_number=attempt_number,
        status_before=status_before,
        status_after=status_after,
        error_detail=error_detail,
        dispatch_result=dispatch_result,
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def _get_latest_pending_or_dispatched_task_attempt(
    session: AsyncSession,
    *,
    task_execution_id: UUID,
    attempt_number: int,
) -> TaskAttempt | None:
    """Load the latest pending-or-dispatched attempt for an attempt number."""
    stmt = (
        select(TaskAttempt)
        .where(
            col(TaskAttempt.task_execution_id) == task_execution_id,
            col(TaskAttempt.attempt_number) == attempt_number,
            col(TaskAttempt.dispatch_result).in_((TaskDispatchResult.PENDING, TaskDispatchResult.DISPATCHED)),
        )
        .order_by(col(TaskAttempt.created_at).desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_task_attempt_dispatch_state(
    session: AsyncSession,
    *,
    task_execution_id: UUID,
    attempt_number: int,
) -> TaskAttempt | None:
    """Load an attempt whose dispatch state can be recovered after a DB failure."""
    return await _get_latest_pending_or_dispatched_task_attempt(
        session,
        task_execution_id=task_execution_id,
        attempt_number=attempt_number,
    )


async def get_latest_pending_task_attempt(
    session: AsyncSession,
    *,
    task_execution_id: UUID,
) -> TaskAttempt | None:
    """Load the latest unresolved dispatch for restart-safe reconciliation."""
    stmt = (
        select(TaskAttempt)
        .where(
            col(TaskAttempt.task_execution_id) == task_execution_id,
            col(TaskAttempt.dispatch_result) == TaskDispatchResult.PENDING,
        )
        .order_by(col(TaskAttempt.created_at).desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_task_attempt_dispatched(
    session: AsyncSession,
    *,
    task_execution_id: UUID,
    attempt_number: int,
) -> bool:
    """Mark the pending audit entry for a sent retry as dispatched idempotently."""
    attempt = await _get_latest_pending_or_dispatched_task_attempt(
        session,
        task_execution_id=task_execution_id,
        attempt_number=attempt_number,
    )
    if attempt is None:
        return False
    if attempt.dispatch_result == TaskDispatchResult.PENDING:
        attempt.dispatch_result = TaskDispatchResult.DISPATCHED
        session.add(attempt)
        await session.flush()
    return True
