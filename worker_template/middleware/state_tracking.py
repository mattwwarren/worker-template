"""State tracking middleware for automatic TaskExecution status updates.

This middleware uses its own session for state updates to ensure task state
is persisted even when the task's transaction rolls back on error.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from worker_template.db.session import async_session_maker
from worker_template.models.task_execution import TaskStatus
from worker_template.realtime.contracts import (
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_STATUS_CHANGED,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStatusEvent,
)
from worker_template.realtime.emitter import emit_task_event
from worker_template.services.task_execution_service import get_task_execution, update_task_status

LOGGER = logging.getLogger(__name__)

_TASK_EXECUTION_ID_KEY = "task_execution_id"
RAW_INPUT_KEY = "raw_input"


class StateTrackingMiddleware(TaskiqMiddleware):
    """Auto-update TaskExecution status in DB during task lifecycle.

    Creates its own AsyncSession for each state update to ensure
    persistence independent of the task's transaction.
    """

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Set task status to RUNNING."""
        task_execution_id = self._extract_task_execution_id(message)
        if task_execution_id is None:
            return message

        async with async_session_maker() as session:
            await update_task_status(
                session,
                task_execution_id,
                TaskStatus.RUNNING,
                status_message="Task started",
            )
            await session.commit()
        await self._emit_status_event(message, TaskStatus.RUNNING, status_message="Task started")
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Set task status to COMPLETED or FAILED based on result."""
        task_execution_id = self._extract_task_execution_id(message)
        if task_execution_id is None:
            return

        async with async_session_maker() as session:
            if result.is_err:
                await update_task_status(
                    session,
                    task_execution_id,
                    TaskStatus.FAILED,
                    error_detail=str(result.error),
                    status_message="Task failed",
                )
            else:
                await update_task_status(
                    session,
                    task_execution_id,
                    TaskStatus.COMPLETED,
                    status_message="Task completed successfully",
                )
            await session.commit()
        if result.is_err:
            await self._emit_status_event(
                message, TaskStatus.FAILED, error_detail=str(result.error), status_message="Task failed"
            )
        else:
            await self._emit_status_event(message, TaskStatus.COMPLETED, status_message="Task completed successfully")

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        """Set task status to FAILED or RETRYING based on retry count."""
        task_execution_id = self._extract_task_execution_id(message)
        if task_execution_id is None:
            return

        status = TaskStatus.FAILED
        status_msg = "Task failed (max retries exceeded)"

        async with async_session_maker() as session:
            # Check if we should retry
            task = await get_task_execution(session, task_execution_id)
            if task is not None and task.retry_count < task.max_retries:
                status = TaskStatus.RETRYING
                status_msg = f"Retrying ({task.retry_count + 1}/{task.max_retries})"

            await update_task_status(
                session,
                task_execution_id,
                status,
                error_detail=f"{type(exception).__name__}: {exception}",
                status_message=status_msg,
            )
            await session.commit()
        await self._emit_status_event(
            message,
            status,
            error_detail=f"{type(exception).__name__}: {exception}",
            status_message=status_msg,
        )

    async def _emit_status_event(
        self,
        message: TaskiqMessage,
        status: TaskStatus,
        *,
        status_message: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Emit a real-time event for a status change. Fire-and-forget."""
        task_execution_id = self._extract_task_execution_id(message)
        if task_execution_id is None:
            return

        # Extract tenant_id from message
        tenant_id = self._extract_tenant_id(message)
        if tenant_id is None:
            return

        task_name = message.task_name

        try:
            if status == TaskStatus.COMPLETED:
                event_data = TaskCompletedEvent(
                    task_id=task_execution_id,
                    task_name=task_name,
                    tenant_id=tenant_id,
                )
                await emit_task_event(tenant_id, TASK_COMPLETED, event_data)
            elif status == TaskStatus.FAILED:
                event_data_failed = TaskFailedEvent(
                    task_id=task_execution_id,
                    task_name=task_name,
                    error_detail=error_detail,
                    tenant_id=tenant_id,
                )
                await emit_task_event(tenant_id, TASK_FAILED, event_data_failed)
            else:
                event_data_status = TaskStatusEvent(
                    task_id=task_execution_id,
                    task_name=task_name,
                    status=status.value,
                    status_message=status_message,
                    tenant_id=tenant_id,
                )
                await emit_task_event(tenant_id, TASK_STATUS_CHANGED, event_data_status)
        except Exception:
            LOGGER.warning("realtime_emit_error", extra={"task_name": task_name}, exc_info=True)

    def _extract_tenant_id(self, message: TaskiqMessage) -> UUID | None:
        """Extract tenant_id from message kwargs or raw_input."""
        tenant_key = "tenant_id"

        if tenant_key in message.kwargs:
            return self._parse_uuid(message.kwargs[tenant_key])

        raw_input = message.kwargs.get(RAW_INPUT_KEY)
        if isinstance(raw_input, dict) and tenant_key in raw_input:
            return self._parse_uuid(raw_input[tenant_key])

        tenant_label = message.labels.get(tenant_key)
        if tenant_label is not None:
            return self._parse_uuid(tenant_label)

        return None

    def _extract_task_execution_id(self, message: TaskiqMessage) -> UUID | None:
        """Extract task_execution_id from message labels or kwargs."""
        # Check sources in priority order: labels, kwargs, raw_input
        candidates: list[object] = []

        label_value = message.labels.get(_TASK_EXECUTION_ID_KEY)
        if label_value is not None:
            candidates.append(label_value)

        if _TASK_EXECUTION_ID_KEY in message.kwargs:
            candidates.append(message.kwargs[_TASK_EXECUTION_ID_KEY])

        raw_input = message.kwargs.get(RAW_INPUT_KEY)
        if isinstance(raw_input, dict) and _TASK_EXECUTION_ID_KEY in raw_input:
            candidates.append(raw_input[_TASK_EXECUTION_ID_KEY])

        for candidate in candidates:
            parsed = self._parse_uuid(candidate)
            if parsed is not None:
                return parsed

        return None

    def _parse_uuid(self, value: object) -> UUID | None:
        """Parse a value to UUID, returning None on failure."""
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (ValueError, AttributeError):
            return None
