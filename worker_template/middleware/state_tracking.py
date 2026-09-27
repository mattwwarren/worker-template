"""State tracking middleware for automatic TaskExecution status updates.

This middleware uses its own session for state updates to ensure task state
is persisted even when the task's transaction rolls back on error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import NoResultError, TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq.kicker import AsyncKicker

from worker_template.core.config import settings
from worker_template.db.retry import db_retry
from worker_template.db.session import async_session_maker
from worker_template.models.task_attempt import TaskDispatchResult
from worker_template.models.task_execution import TaskExecution, TaskStatus
from worker_template.realtime.contracts import (
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_STATUS_CHANGED,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStatusEvent,
)
from worker_template.realtime.emitter import emit_task_event
from worker_template.services.task_attempt_service import (
    get_latest_pending_task_attempt,
    get_task_attempt_dispatch_state,
    mark_task_attempt_dispatched,
    record_task_attempt,
)
from worker_template.services.task_execution_service import get_task_execution, update_task_status

LOGGER = logging.getLogger(__name__)

_TASK_EXECUTION_ID_KEY = "task_execution_id"
RAW_INPUT_KEY = "raw_input"
_SHADOW_RETRY_MARKER = "_state_tracking_shadow_retry"
_MISSING_RETRY_AUDIT_DETAIL = "missing retry audit row"
_RECOVERY_AUDIT_NOT_FOUND = "retry recovery found no matching audit row"
_RECONCILIATION_AUDIT_NOT_FOUND = "retry dispatch reconciliation found no matching audit row"
_DISPATCH_AUDIT_NOT_FOUND = "retry dispatch completed but its audit row was not found"
_RECOVERED_AUDIT_NOT_FOUND = "retry dispatch recovery found no matching audit row"


def _missing_retry_audit_error(message: str) -> OperationalError:
    """Create a retryable error for an unresolvable dispatch audit."""
    detail = _MISSING_RETRY_AUDIT_DETAIL
    return OperationalError(message, {}, RuntimeError(detail))


@dataclass
class _OnErrorAttempt:
    """Track the status mutation attempted by one on_error invocation."""

    recorded_status: TaskStatus | None = None
    recorded_retry_count: int | None = None
    status_message: str | None = None
    attempt_number: int | None = None


@dataclass
class _RetryDecision:
    """What one on_error invocation decided to do about retrying, and why."""

    status: TaskStatus
    status_msg: str
    retry_already_recorded: bool
    should_retry: bool
    retry_allowed: bool
    retry_shadowed: bool
    status_before: TaskStatus
    attempt_number: int


@dataclass
class _ErrorContext:
    """Shared invocation state threaded through on_error's retry-decision helpers."""

    session: AsyncSession
    task: TaskExecution | None
    task_execution_id: UUID
    attempt: _OnErrorAttempt
    message: TaskiqMessage
    result: TaskiqResult[Any]
    error_detail: str


class StateTrackingMiddleware(TaskiqMiddleware):
    """Auto-update TaskExecution status in DB during task lifecycle.

    Creates its own AsyncSession for each state update to ensure
    persistence independent of the task's transaction.
    """

    # @db_retry wraps the whole method, not just the DB block, so each retry
    # attempt opens a fresh session rather than reusing one left in a bad
    # state by the failed commit.
    @db_retry
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

    @db_retry
    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Set task status to COMPLETED or FAILED based on result."""
        task_execution_id = self._extract_task_execution_id(message)
        if task_execution_id is None:
            return

        if isinstance(result.error, NoResultError):
            # on_error already dispatched a retry and owns this message's outcome.
            return
        if result.__dict__.get(_SHADOW_RETRY_MARKER, False):
            # on_error recorded a shadowed retry and owns this message's outcome.
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

        await self._record_error_status(
            message,
            result,
            exception,
            task_execution_id,
            _OnErrorAttempt(),
        )

    @db_retry
    async def _record_error_status(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
        task_execution_id: UUID,
        attempt: _OnErrorAttempt,
    ) -> None:
        """Record an error, retrying with invocation-scoped idempotency state."""
        error_detail = f"{type(exception).__name__}: {exception}"

        async with async_session_maker() as session:
            task = await get_task_execution(session, task_execution_id)
            ctx = _ErrorContext(
                session=session,
                task=task,
                task_execution_id=task_execution_id,
                attempt=attempt,
                message=message,
                result=result,
                error_detail=error_detail,
            )
            decision = self._resolve_retry_decision(ctx)
            status, status_msg = decision.status, decision.status_msg

            if decision.retry_allowed:
                if decision.retry_already_recorded:
                    recovered_attempt = await get_task_attempt_dispatch_state(
                        session,
                        task_execution_id=task_execution_id,
                        attempt_number=attempt.attempt_number or decision.attempt_number,
                    )
                    if (
                        recovered_attempt is not None
                        and recovered_attempt.dispatch_result == TaskDispatchResult.DISPATCHED
                    ):
                        await self._recover_dispatch_audit(ctx, recovered_attempt.attempt_number)
                    elif recovered_attempt is None:
                        raise _missing_retry_audit_error(_RECOVERY_AUDIT_NOT_FOUND)
                    else:
                        await session.commit()
                        status, status_msg = await self._dispatch_retry(ctx, decision)
                else:
                    pending_attempt = await get_latest_pending_task_attempt(
                        session,
                        task_execution_id=task_execution_id,
                    )
                    if pending_attempt is not None:
                        marked = await mark_task_attempt_dispatched(
                            session,
                            task_execution_id=task_execution_id,
                            attempt_number=pending_attempt.attempt_number,
                        )
                        if not marked:
                            raise _missing_retry_audit_error(_RECONCILIATION_AUDIT_NOT_FOUND)
                    await self._record_initial_error_status(ctx, decision)
                    await session.commit()
                    status, status_msg = await self._dispatch_retry(ctx, decision)
            else:
                if not decision.retry_already_recorded:
                    await self._record_initial_error_status(ctx, decision)
                await session.commit()

            if decision.retry_shadowed:
                result.__dict__[_SHADOW_RETRY_MARKER] = True

        await self._emit_status_event(
            message,
            status,
            error_detail=error_detail,
            status_message=status_msg,
        )

    def _resolve_retry_decision(self, ctx: _ErrorContext) -> _RetryDecision:
        """Decide the status/message for this error and whether a retry is due."""
        task = ctx.task
        attempt = ctx.attempt
        status = TaskStatus.FAILED
        status_msg = "Task failed (max retries exceeded)"

        retry_already_recorded = (
            attempt.recorded_status is not None
            and attempt.recorded_retry_count is not None
            and task is not None
            and task.status == attempt.recorded_status
            and task.retry_count == attempt.recorded_retry_count
            and task.status_message == attempt.status_message
        )
        should_retry = task is not None and task.retry_count < task.max_retries
        retry_allowed = (
            should_retry and task is not None and self._retry_gate_allows(ctx.message.task_name, task.tenant_id)
        )
        retry_shadowed = should_retry and not retry_allowed and settings.task_retry_shadow_mode
        status_before = task.status if task is not None else TaskStatus.RUNNING
        attempt_number = task.retry_count + 1 if task is not None else 1

        if retry_already_recorded:
            assert attempt.recorded_status is not None
            status = attempt.recorded_status
            status_msg = attempt.status_message or status_msg
        elif should_retry:
            if retry_allowed:
                status = TaskStatus.RETRYING
                status_msg = f"Retrying ({task.retry_count + 1}/{task.max_retries})"  # type: ignore[union-attr]
            elif retry_shadowed:
                status = TaskStatus.RETRYING
                status_msg = "Retry shadowed (automatic retry disabled)"
            else:
                status = TaskStatus.FAILED
                status_msg = "Task failed (automatic retry disabled)"

        return _RetryDecision(
            status=status,
            status_msg=status_msg,
            retry_already_recorded=retry_already_recorded,
            should_retry=should_retry,
            retry_allowed=retry_allowed,
            retry_shadowed=retry_shadowed,
            status_before=status_before,
            attempt_number=attempt_number,
        )

    async def _record_initial_error_status(self, ctx: _ErrorContext, decision: _RetryDecision) -> None:
        """Persist the first status/audit write for this on_error invocation."""
        task = ctx.task
        attempt = ctx.attempt
        expected_retry_count = task.retry_count if task is not None else None
        if decision.status == TaskStatus.RETRYING and expected_retry_count is not None:
            expected_retry_count += 1
        await update_task_status(
            ctx.session,
            ctx.task_execution_id,
            decision.status,
            error_detail=ctx.error_detail,
            status_message=decision.status_msg,
        )
        attempt.recorded_status = decision.status
        attempt.recorded_retry_count = expected_retry_count
        attempt.status_message = decision.status_msg

        if decision.should_retry and not decision.retry_allowed:
            dispatch_result = TaskDispatchResult.SHADOWED if decision.retry_shadowed else TaskDispatchResult.BLOCKED
            await record_task_attempt(
                ctx.session,
                task_execution_id=ctx.task_execution_id,
                tenant_id=task.tenant_id,  # type: ignore[union-attr]
                attempt_number=decision.attempt_number,
                status_before=decision.status_before,
                status_after=decision.status,
                error_detail=ctx.error_detail,
                dispatch_result=dispatch_result,
            )
        elif decision.retry_allowed:
            attempt.attempt_number = decision.attempt_number
            await record_task_attempt(
                ctx.session,
                task_execution_id=ctx.task_execution_id,
                tenant_id=task.tenant_id,  # type: ignore[union-attr]
                attempt_number=decision.attempt_number,
                status_before=decision.status_before,
                status_after=TaskStatus.RETRYING,
                error_detail=ctx.error_detail,
                dispatch_result=TaskDispatchResult.PENDING,
            )

    async def _dispatch_retry(self, ctx: _ErrorContext, decision: _RetryDecision) -> tuple[TaskStatus, str]:
        """Requeue the message and record the outcome. Returns the final (status, message)."""
        task = ctx.task
        requeued = await self._requeue(ctx.message)
        if requeued:
            marked = await mark_task_attempt_dispatched(
                ctx.session,
                task_execution_id=ctx.task_execution_id,
                attempt_number=ctx.attempt.attempt_number or decision.attempt_number,
            )
            if not marked:
                raise _missing_retry_audit_error(_DISPATCH_AUDIT_NOT_FOUND)
            await ctx.session.commit()
            ctx.result.error = NoResultError()
            return decision.status, decision.status_msg

        status = TaskStatus.FAILED
        status_msg = "Task failed (retry dispatch error)"
        await update_task_status(
            ctx.session,
            ctx.task_execution_id,
            status,
            error_detail=ctx.error_detail,
            status_message=status_msg,
        )
        ctx.attempt.recorded_status = status
        ctx.attempt.status_message = status_msg
        await record_task_attempt(
            ctx.session,
            task_execution_id=ctx.task_execution_id,
            tenant_id=task.tenant_id,  # type: ignore[union-attr]
            attempt_number=decision.attempt_number,
            status_before=TaskStatus.RETRYING,
            status_after=status,
            error_detail=ctx.error_detail,
            dispatch_result=TaskDispatchResult.DISPATCH_FAILED,
        )
        await ctx.session.commit()
        return status, status_msg

    async def _recover_dispatch_audit(self, ctx: _ErrorContext, attempt_number: int) -> None:
        """Repair dispatch audit state after the broker send preceded a DB failure."""
        marked = await mark_task_attempt_dispatched(
            ctx.session,
            task_execution_id=ctx.task_execution_id,
            attempt_number=attempt_number,
        )
        if not marked:
            raise _missing_retry_audit_error(_RECOVERED_AUDIT_NOT_FOUND)
        await ctx.session.commit()
        ctx.result.error = NoResultError()

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
        if not tenant_allowlist or not task_allowlist:
            return False
        return str(tenant_id) in tenant_allowlist and task_name in task_allowlist

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
