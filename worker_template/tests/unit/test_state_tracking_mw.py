"""Tests for StateTrackingMiddleware: pre_execute, post_execute, on_error with mocked DB."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from taskiq import NoResultError

from worker_template.middleware.state_tracking import StateTrackingMiddleware
from worker_template.models.task_execution import TaskStatus


def make_message(task_name="test_task", labels=None, kwargs=None, args=None):
    """Create a mock TaskIQ message."""
    msg = MagicMock()
    msg.task_id = str(uuid4())
    msg.task_name = task_name
    msg.args = args if args is not None else []
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


class TestStateTrackingPreExecute:
    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_sets_status_running(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            result = await middleware.pre_execute(msg)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.RUNNING,
                status_message="Task started",
            )
            mock_session.commit.assert_called_once()
            assert result is msg

    async def test_skips_when_no_task_execution_id(self, middleware):
        msg = make_message(labels={}, kwargs={})

        with patch("worker_template.middleware.state_tracking.update_task_status") as mock_update:
            result = await middleware.pre_execute(msg)

            mock_update.assert_not_called()
            assert result is msg


class TestStateTrackingPostExecute:
    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_sets_completed_on_success(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=False)
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            await middleware.post_execute(msg, result)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.COMPLETED,
                status_message="Task completed successfully",
            )
            mock_session.commit.assert_called_once()

    async def test_sets_failed_on_error_result(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True, error="Something went wrong")
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            await middleware.post_execute(msg, result)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.FAILED,
                error_detail="Something went wrong",
                status_message="Task failed",
            )

    async def test_skips_when_no_task_execution_id(self, middleware):
        msg = make_message(labels={}, kwargs={})
        result = make_result(is_err=False)

        with patch("worker_template.middleware.state_tracking.update_task_status") as mock_update:
            await middleware.post_execute(msg, result)

            mock_update.assert_not_called()

    async def test_skips_when_retry_already_dispatched(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True, error=NoResultError())
        mock_maker = MagicMock()

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            await middleware.post_execute(msg, result)

            mock_maker.assert_not_called()
            mock_update.assert_not_called()


class TestStateTrackingOnError:
    @pytest.fixture
    def middleware(self, monkeypatch):
        monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_enabled", True)
        monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_shadow_mode", False)
        return StateTrackingMiddleware()

    async def test_sets_retrying_when_retries_available(self, middleware, monkeypatch):
        task_exec_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)}, kwargs={"foo": "bar"}, args=[1, 2])
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3
        mock_task.status = TaskStatus.RUNNING
        mock_task.tenant_id = tenant_id
        monkeypatch.setattr(
            "worker_template.middleware.state_tracking.settings.task_retry_tenant_allowlist", str(tenant_id)
        )
        monkeypatch.setattr(
            "worker_template.middleware.state_tracking.settings.task_retry_task_allowlist", msg.task_name
        )

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt") as mock_audit,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            mock_kicker = mock_kicker_cls.return_value
            mock_kicker.with_task_id.return_value = mock_kicker
            mock_kicker.kiq = AsyncMock()

            await middleware.on_error(msg, result, exc)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.RETRYING,
                error_detail="RuntimeError: crash",
                status_message="Retrying (1/3)",
            )
            assert mock_session.commit.await_count == 2

            mock_kicker_cls.assert_called_once_with(
                task_name=msg.task_name,
                broker=middleware.broker,
                labels=msg.labels,
            )
            mock_kicker.with_task_id.assert_called_once_with(msg.task_id)
            mock_kicker.kiq.assert_awaited_once_with(*msg.args, **msg.kwargs)
            assert mock_audit.await_count == 2
            assert isinstance(result.error, NoResultError)

    async def test_shadow_mode_records_without_requeue(self, middleware, monkeypatch):
        monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_enabled", False)
        monkeypatch.setattr("worker_template.middleware.state_tracking.settings.task_retry_shadow_mode", True)
        task_exec_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3
        mock_task.status = TaskStatus.RUNNING
        mock_task.tenant_id = tenant_id

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt") as mock_audit,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            await middleware.on_error(msg, result, exc)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.RETRYING,
                error_detail="RuntimeError: crash",
                status_message="Retry shadowed (automatic retry disabled)",
            )
            mock_kicker_cls.assert_not_called()
            mock_audit.assert_awaited_once_with(
                mock_session,
                task_execution_id=task_exec_id,
                tenant_id=tenant_id,
                attempt_number=1,
                status_before=TaskStatus.RUNNING,
                status_after=TaskStatus.RETRYING,
                error_detail="RuntimeError: crash",
                dispatch_result="shadowed",
            )
            assert result.error is None

    async def test_sets_failed_when_max_retries_exceeded(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = ValueError("bad input")

        mock_task = MagicMock()
        mock_task.retry_count = 3
        mock_task.max_retries = 3

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            await middleware.on_error(msg, result, exc)

            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.FAILED,
                error_detail="ValueError: bad input",
                status_message="Task failed (max retries exceeded)",
            )
            mock_kicker_cls.assert_not_called()
            assert result.error is None

    async def test_sets_failed_when_task_not_found(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=None),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            await middleware.on_error(msg, result, exc)

            # When task is None, it should go to FAILED path
            mock_update.assert_called_once_with(
                mock_session,
                task_exec_id,
                TaskStatus.FAILED,
                error_detail="RuntimeError: crash",
                status_message="Task failed (max retries exceeded)",
            )
            mock_kicker_cls.assert_not_called()
            assert result.error is None

    async def test_retry_dispatch_failure_reconciles_to_failed(self, middleware, monkeypatch):
        task_exec_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3
        mock_task.status = TaskStatus.RUNNING
        mock_task.tenant_id = tenant_id
        monkeypatch.setattr(
            "worker_template.middleware.state_tracking.settings.task_retry_tenant_allowlist", str(tenant_id)
        )
        monkeypatch.setattr(
            "worker_template.middleware.state_tracking.settings.task_retry_task_allowlist", msg.task_name
        )

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt") as mock_audit,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            mock_kicker = mock_kicker_cls.return_value
            mock_kicker.with_task_id.return_value = mock_kicker
            mock_kicker.kiq = AsyncMock(side_effect=RuntimeError("broker unreachable"))

            await middleware.on_error(msg, result, exc)

            assert mock_update.call_count == 2
            mock_update.assert_any_call(
                mock_session,
                task_exec_id,
                TaskStatus.RETRYING,
                error_detail="RuntimeError: crash",
                status_message="Retrying (1/3)",
            )
            mock_update.assert_any_call(
                mock_session,
                task_exec_id,
                TaskStatus.FAILED,
                error_detail="RuntimeError: crash",
                status_message="Task failed (retry dispatch error)",
            )
            assert mock_audit.await_count == 2
            assert result.error is None

    async def test_skips_when_no_task_execution_id(self, middleware):
        msg = make_message(labels={}, kwargs={})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        with patch("worker_template.middleware.state_tracking.update_task_status") as mock_update:
            await middleware.on_error(msg, result, exc)

            mock_update.assert_not_called()


class TestRequeue:
    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    async def test_requeue_success_forwards_message_fields(self, middleware):
        msg = make_message(
            task_name="flaky_task",
            labels={"task_execution_id": "abc", "tenant_id": "xyz"},
            kwargs={"raw_input": {"foo": "bar"}},
            args=[1, "two"],
        )

        with patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls:
            mock_kicker = mock_kicker_cls.return_value
            mock_kicker.with_task_id.return_value = mock_kicker
            mock_kicker.kiq = AsyncMock()

            requeued = await middleware._requeue(msg)

            assert requeued is True
            mock_kicker_cls.assert_called_once_with(
                task_name=msg.task_name,
                broker=middleware.broker,
                labels=msg.labels,
            )
            mock_kicker.with_task_id.assert_called_once_with(msg.task_id)
            mock_kicker.kiq.assert_awaited_once_with(*msg.args, **msg.kwargs)

    async def test_requeue_failure_returns_false(self, middleware):
        msg = make_message()

        with patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls:
            mock_kicker = mock_kicker_cls.return_value
            mock_kicker.with_task_id.return_value = mock_kicker
            mock_kicker.kiq = AsyncMock(side_effect=RuntimeError("broker unreachable"))

            requeued = await middleware._requeue(msg)

            assert requeued is False


class TestStateTrackingExtractUUID:
    """Test _extract_task_execution_id and _parse_uuid."""

    def test_uuid_object_passthrough(self):
        mw = StateTrackingMiddleware()
        task_id = uuid4()
        msg = MagicMock()
        msg.labels = {"task_execution_id": task_id}
        msg.kwargs = {}

        result = mw._extract_task_execution_id(msg)
        assert result == task_id

    def test_priority_labels_over_kwargs(self):
        mw = StateTrackingMiddleware()
        label_id = uuid4()
        kwarg_id = uuid4()
        msg = MagicMock()
        msg.labels = {"task_execution_id": str(label_id)}
        msg.kwargs = {"task_execution_id": str(kwarg_id)}

        result = mw._extract_task_execution_id(msg)
        assert result == label_id
