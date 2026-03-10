"""Tests for state tracking middleware's Socket.IO event emission."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from worker_template.middleware.state_tracking import StateTrackingMiddleware
from worker_template.models.task_execution import TaskStatus
from worker_template.realtime.contracts import (
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_STATUS_CHANGED,
)


def make_message(task_name="test_task", labels=None, kwargs=None):
    """Create a mock TaskIQ message."""
    msg = MagicMock()
    msg.task_id = str(uuid4())
    msg.task_name = task_name
    msg.kwargs = kwargs or {}
    msg.labels = labels or {}
    return msg


def make_result(is_err=False, error=None):
    """Create a mock TaskIQ result."""
    result = MagicMock()
    result.is_err = is_err
    result.error = error
    return result


def make_mock_session():
    """Create a mock async session that supports async context manager."""
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_ctx


class TestEmitStatusEvent:
    """Tests for the _emit_status_event private method."""

    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_emits_status_changed_for_running(self, middleware):
        """_emit_status_event emits TASK_STATUS_CHANGED for RUNNING status."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {
                "task_execution_id": str(task_id),
                "tenant_id": str(tenant_id),
            }},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            await middleware._emit_status_event(msg, TaskStatus.RUNNING, status_message="Task started")

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == tenant_id
            assert call_args[0][1] == TASK_STATUS_CHANGED
            event_data = call_args[0][2]
            assert event_data.task_id == task_id
            assert event_data.status == TaskStatus.RUNNING.value

    async def test_emits_completed_event(self, middleware):
        """_emit_status_event emits TASK_COMPLETED for COMPLETED status."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {
                "task_execution_id": str(task_id),
                "tenant_id": str(tenant_id),
            }},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            await middleware._emit_status_event(msg, TaskStatus.COMPLETED)

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == tenant_id
            assert call_args[0][1] == TASK_COMPLETED
            event_data = call_args[0][2]
            assert event_data.task_id == task_id

    async def test_emits_failed_event(self, middleware):
        """_emit_status_event emits TASK_FAILED for FAILED status."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {
                "task_execution_id": str(task_id),
                "tenant_id": str(tenant_id),
            }},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            await middleware._emit_status_event(
                msg, TaskStatus.FAILED, error_detail="Something broke"
            )

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == tenant_id
            assert call_args[0][1] == TASK_FAILED
            event_data = call_args[0][2]
            assert event_data.task_id == task_id
            assert event_data.error_detail == "Something broke"

    async def test_noop_without_tenant_id(self, middleware):
        """_emit_status_event is a no-op when tenant_id is not available."""
        task_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {"task_execution_id": str(task_id)}},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            await middleware._emit_status_event(msg, TaskStatus.RUNNING)

            mock_emit.assert_not_awaited()

    async def test_noop_without_task_execution_id(self, middleware):
        """_emit_status_event is a no-op when task_execution_id is not available."""
        tenant_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {"tenant_id": str(tenant_id)}},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
        ) as mock_emit:
            await middleware._emit_status_event(msg, TaskStatus.RUNNING)

            mock_emit.assert_not_awaited()

    async def test_swallows_emit_exception(self, middleware):
        """_emit_status_event catches and logs exceptions without raising."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            kwargs={"raw_input": {
                "task_execution_id": str(task_id),
                "tenant_id": str(tenant_id),
            }},
        )

        with patch(
            "worker_template.middleware.state_tracking.emit_task_event",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis down"),
        ):
            # Should NOT raise
            await middleware._emit_status_event(msg, TaskStatus.RUNNING)


class TestPreExecuteEmit:
    """Tests that pre_execute emits events after DB update."""

    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_emits_running_event(self, middleware):
        """pre_execute emits RUNNING status event after DB commit."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            labels={"task_execution_id": str(task_id)},
            kwargs={"raw_input": {"tenant_id": str(tenant_id)}},
        )
        _mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status", new_callable=AsyncMock),
            patch(
                "worker_template.middleware.state_tracking.emit_task_event",
                new_callable=AsyncMock,
            ) as mock_emit,
        ):
            await middleware.pre_execute(msg)

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == tenant_id
            assert call_args[0][1] == TASK_STATUS_CHANGED


class TestPostExecuteEmit:
    """Tests that post_execute emits events after DB update."""

    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_emits_completed_on_success(self, middleware):
        """post_execute emits TASK_COMPLETED event on success."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            labels={"task_execution_id": str(task_id)},
            kwargs={"raw_input": {"tenant_id": str(tenant_id)}},
        )
        result = make_result(is_err=False)
        _mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status", new_callable=AsyncMock),
            patch(
                "worker_template.middleware.state_tracking.emit_task_event",
                new_callable=AsyncMock,
            ) as mock_emit,
        ):
            await middleware.post_execute(msg, result)

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][1] == TASK_COMPLETED

    async def test_emits_failed_on_error(self, middleware):
        """post_execute emits TASK_FAILED event on error."""
        task_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(
            labels={"task_execution_id": str(task_id)},
            kwargs={"raw_input": {"tenant_id": str(tenant_id)}},
        )
        result = make_result(is_err=True, error="Something broke")
        _mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status", new_callable=AsyncMock),
            patch(
                "worker_template.middleware.state_tracking.emit_task_event",
                new_callable=AsyncMock,
            ) as mock_emit,
        ):
            await middleware.post_execute(msg, result)

            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][1] == TASK_FAILED


class TestExtractTenantId:
    """Tests for _extract_tenant_id method."""

    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    def test_extract_from_direct_kwarg(self, middleware):
        tenant_id = uuid4()
        msg = make_message(kwargs={"tenant_id": str(tenant_id)})
        assert middleware._extract_tenant_id(msg) == tenant_id

    def test_extract_from_raw_input(self, middleware):
        tenant_id = uuid4()
        msg = make_message(kwargs={"raw_input": {"tenant_id": str(tenant_id)}})
        assert middleware._extract_tenant_id(msg) == tenant_id

    def test_extract_from_labels(self, middleware):
        tenant_id = uuid4()
        msg = make_message(labels={"tenant_id": str(tenant_id)})
        assert middleware._extract_tenant_id(msg) == tenant_id

    def test_returns_none_when_missing(self, middleware):
        msg = make_message(kwargs={}, labels={})
        assert middleware._extract_tenant_id(msg) is None

    def test_returns_none_for_invalid_uuid(self, middleware):
        msg = make_message(kwargs={"tenant_id": "not-a-uuid"})
        assert middleware._extract_tenant_id(msg) is None
