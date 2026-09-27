"""Data access service for append-only task retry audit records."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

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
    """Append a durable record for one retry decision."""
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
