"""State tracking middleware for automatic TaskExecution status updates.

This middleware uses its own session for state updates to ensure task state
is persisted even when the task's transaction rolls back on error.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from taskiq import NoResultError, TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq.kicker import AsyncKicker

from worker_template.core.config import settings
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
from worker_template.services.task_attempt_service import record_task_attempt
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

        if isinstance(result.error, NoResultError):
            # on_error already dispatched a retry and owns this message's outcome.
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
        error_detail = f"{type(exception).__name__}: {exception}"

        async with async_session_maker() as session:
            # Check if we should retry
            task = await get_task_execution(session, task_execution_id)
            should_retry = task is not None and task.retry_count < task.max_retries
            retry_allowed = (
                should_retry and task is not None and self._retry_gate_allows(message.task_name, task.tenant_id)
            )
            retry_shadowed = should_retry and not retry_allowed and settings.task_retry_shadow_mode
            status_before = task.status if task is not None else TaskStatus.RUNNING
            attempt_number = task.retry_count + 1 if task is not None else 1
            if should_retry:
                if retry_allowed:
                    status = TaskStatus.RETRYING
                    status_msg = f"Retrying ({task.retry_count + 1}/{task.max_retries})"  # type: ignore[union-attr]
                else:
                    status = TaskStatus.FAILED
                    status_msg = (
                        "Retry shadowed (automatic retry disabled)"
                        if retry_shadowed
                        else "Task failed (automatic retry disabled)"
                    )

            await update_task_status(
                session,
                task_execution_id,
                status,
                error_detail=error_detail,
                status_message=status_msg,
            )
            if should_retry and not retry_allowed:
                await record_task_attempt(
                    session,
                    task_execution_id=task_execution_id,
                    tenant_id=task.tenant_id,  # type: ignore[union-attr]
                    attempt_number=attempt_number,
                    status_before=status_before,
                    status_after=status,
                    error_detail=error_detail,
                    dispatch_result="shadowed" if retry_shadowed else "blocked",
                )
            elif retry_allowed:
                await record_task_attempt(
                    session,
                    task_execution_id=task_execution_id,
                    tenant_id=task.tenant_id,  # type: ignore[union-attr]
                    attempt_number=attempt_number,
                    status_before=status_before,
                    status_after=TaskStatus.RETRYING,
                    error_detail=error_detail,
                    dispatch_result="pending",
                )
            await session.commit()

            if retry_allowed:
                requeued = await self._requeue(message)
                if requeued:
                    await record_task_attempt(
                        session,
                        task_execution_id=task_execution_id,
                        tenant_id=task.tenant_id,  # type: ignore[union-attr]
                        attempt_number=attempt_number,
                        status_before=TaskStatus.RETRYING,
                        status_after=TaskStatus.RETRYING,
                        error_detail=error_detail,
                        dispatch_result="dispatched",
                    )
                    await session.commit()
                    result.error = NoResultError()
                else:
                    status = TaskStatus.FAILED
                    status_msg = "Task failed (retry dispatch error)"
                    await update_task_status(
                        session,
                        task_execution_id,
                        status,
                        error_detail=error_detail,
                        status_message=status_msg,
                    )
                    await record_task_attempt(
                        session,
                        task_execution_id=task_execution_id,
                        tenant_id=task.tenant_id,  # type: ignore[union-attr]
                        attempt_number=attempt_number,
                        status_before=TaskStatus.RETRYING,
                        status_after=status,
                        error_detail=error_detail,
                        dispatch_result="dispatch_failed",
                    )
                    await session.commit()

        await self._emit_status_event(
            message,
            status,
            error_detail=error_detail,
            status_message=status_msg,
        )

    async def _requeue(self, message: TaskiqMessage) -> bool:
        """Re-enqueue the message for a retry attempt under the same task_id."""
        try:
            kicker: AsyncKicker[Any, Any] = AsyncKicker(
                task_name=message.task_name,
                broker=self.broker,
                labels=dict(message.labels),
            ).with_task_id(message.task_id)
            await kicker.kiq(*message.args, **message.kwargs)
        except Exception:
            LOGGER.warning("task_requeue_error", extra={"task_name": message.task_name}, exc_info=True)
            return False
        return True

    def _retry_gate_allows(self, task_name: str, tenant_id: UUID) -> bool:
        """Return whether automatic retries are enabled for this task and tenant."""
        if not settings.task_retry_enabled:
            return False

        tenant_allowlist = {
            value.strip() for value in settings.task_retry_tenant_allowlist.split(",") if value.strip()
        }
        task_allowlist = {value.strip() for value in settings.task_retry_task_allowlist.split(",") if value.strip()}
        if not tenant_allowlist and not task_allowlist:
            return True
        return str(tenant_id) in tenant_allowlist or task_name in task_allowlist

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
        except ValueError, AttributeError:
            return None
