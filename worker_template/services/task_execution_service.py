"""Task execution data access service."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from worker_template.core.logging import get_logging_context
from worker_template.models.task_execution import TaskExecution, TaskStatus

LOGGER = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.PARTIAL,
    }
)


async def create_task_execution(
    session: AsyncSession,
    *,
    task_name: str,
    tenant_id: UUID,
    config_snapshot: dict[str, object] | None = None,
    parent_task_id: UUID | None = None,
    max_retries: int = 3,
    total_steps: int | None = None,
) -> TaskExecution:
    """Create a new task execution record."""
    context = get_logging_context()
    task = TaskExecution(
        task_name=task_name,
        tenant_id=tenant_id,
        config_snapshot=config_snapshot,
        parent_task_id=parent_task_id,
        max_retries=max_retries,
        total_steps=total_steps,
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)
    LOGGER.info("task_execution_created", extra={**context, "task_execution_id": str(task.id)})
    return task


async def get_task_execution(
    session: AsyncSession,
    task_id: UUID,
    tenant_id: UUID | None = None,
) -> TaskExecution | None:
    """Fetch a task execution by ID, optionally scoped to tenant."""
    stmt = select(TaskExecution).where(col(TaskExecution.id) == task_id)
    if tenant_id is not None:
        stmt = stmt.where(col(TaskExecution.tenant_id) == tenant_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_task_status(
    session: AsyncSession,
    task_id: UUID,
    status: TaskStatus,
    *,
    status_message: str | None = None,
    error_detail: str | None = None,
    result_url: str | None = None,
    completed_steps: int | None = None,
) -> TaskExecution | None:
    """Update task execution status and optional fields."""
    task = await get_task_execution(session, task_id)
    if task is None:
        return None

    task.status = status
    if status_message is not None:
        task.status_message = status_message
    if error_detail is not None:
        task.error_detail = error_detail
    if result_url is not None:
        task.result_url = result_url
    if completed_steps is not None:
        task.completed_steps = completed_steps

    now = datetime.now(UTC)
    if status == TaskStatus.RUNNING and task.started_at is None:
        task.started_at = now
    if status in _TERMINAL_STATUSES:
        task.completed_at = now
    if status == TaskStatus.RETRYING:
        task.retry_count += 1

    session.add(task)
    await session.flush()
    await session.refresh(task)
    return task


async def list_task_executions(
    session: AsyncSession,
    tenant_id: UUID,
    status_filter: TaskStatus | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[TaskExecution]:
    """List task executions for a tenant."""
    stmt = select(TaskExecution).where(col(TaskExecution.tenant_id) == tenant_id)
    if status_filter is not None:
        stmt = stmt.where(col(TaskExecution.status) == status_filter)
    stmt = stmt.order_by(col(TaskExecution.created_at).desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_child_tasks(
    session: AsyncSession,
    parent_task_id: UUID,
) -> list[TaskExecution]:
    """Get all child tasks of a parent task."""
    stmt = (
        select(TaskExecution)
        .where(col(TaskExecution.parent_task_id) == parent_task_id)
        .order_by(col(TaskExecution.created_at))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
