"""Tests for StateTrackingMiddleware: pre_execute, post_execute, on_error with mocked DB."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError
from taskiq import NoResultError

from worker_template.db.retry import DEFAULT_MAX_ATTEMPTS
from worker_template.middleware.state_tracking import StateTrackingMiddleware
from worker_template.models.task_execution import TaskStatus

_STATE_TRACKING_SETTINGS = "worker_template.middleware.state_tracking.settings"


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

    async def test_skips_when_retry_is_shadowed(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True, error=RuntimeError("Something went wrong"))
        result.__dict__["_state_tracking_shadow_retry"] = True
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
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_enabled", True)
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_shadow_mode", False)
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
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_tenant_allowlist", str(tenant_id))
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_task_allowlist", msg.task_name)

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
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_enabled", False)
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_shadow_mode", True)
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
            assert result.__dict__["_state_tracking_shadow_retry"] is True

    async def test_increments_retrying_task_on_new_on_error_invocation(self, middleware, monkeypatch):
        task_exec_id = uuid4()
        tenant_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock(status=TaskStatus.RETRYING, retry_count=1, max_retries=3, tenant_id=tenant_id)
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_tenant_allowlist", str(tenant_id))
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_task_allowlist", msg.task_name)

        async def update_status(_session, _task_id, status, **_kwargs):
            mock_task.status = status
            if status == TaskStatus.RETRYING:
                mock_task.retry_count += 1

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch(
                "worker_template.middleware.state_tracking.update_task_status",
                side_effect=update_status,
            ) as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt") as mock_audit,
            patch("worker_template.middleware.state_tracking.AsyncKicker") as mock_kicker_cls,
        ):
            mock_kicker = mock_kicker_cls.return_value
            mock_kicker.with_task_id.return_value = mock_kicker
            mock_kicker.kiq = AsyncMock()

            await middleware.on_error(msg, result, exc)

        assert mock_task.retry_count == 2
        mock_update.assert_called_once_with(
            mock_session,
            task_exec_id,
            TaskStatus.RETRYING,
            error_detail="RuntimeError: crash",
            status_message="Retrying (2/3)",
        )
        assert mock_audit.await_count == 2
        assert isinstance(result.error, NoResultError)

    async def test_fails_when_existing_retrying_task_reaches_max_retries(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock(status=TaskStatus.RETRYING, retry_count=3, max_retries=3)
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            await middleware.on_error(msg, result, exc)

        mock_update.assert_called_once_with(
            mock_session,
            task_exec_id,
            TaskStatus.FAILED,
            error_detail="RuntimeError: crash",
            status_message="Task failed (max retries exceeded)",
        )

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
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_tenant_allowlist", str(tenant_id))
        monkeypatch.setattr(f"{_STATE_TRACKING_SETTINGS}.task_retry_task_allowlist", msg.task_name)

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


class TestStateTrackingRetryBehavior:
    """Verify @db_retry retries transient OperationalErrors on DB commits."""

    @pytest.fixture
    def middleware(self):
        return StateTrackingMiddleware()

    @pytest.fixture(autouse=True)
    def no_sleep(self):
        with patch("asyncio.sleep", new=AsyncMock()):
            yield

    async def test_pre_execute_retries_on_operational_error_then_succeeds(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            mock_update.side_effect = [OperationalError("stmt", {}, Exception("conn lost")), None]
            result = await middleware.pre_execute(msg)

            assert mock_maker.call_count == 2
            mock_session.commit.assert_called_once()
            assert result is msg

    async def test_post_execute_retries_on_operational_error_then_succeeds(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=False)
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            mock_update.side_effect = [OperationalError("stmt", {}, Exception("conn lost")), None]
            await middleware.post_execute(msg, result)

            assert mock_maker.call_count == 2
            mock_session.commit.assert_called_once()

    async def test_on_error_retries_on_operational_error_then_succeeds(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3

        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.get_task_execution", return_value=mock_task),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt"),
        ):
            mock_update.side_effect = [OperationalError("stmt", {}, Exception("conn lost")), None]
            await middleware.on_error(msg, result, exc)

            assert mock_maker.call_count == 2
            mock_session.commit.assert_called_once()

    async def test_on_error_does_not_increment_retry_count_after_commit_takes_effect(self, middleware):
        # The retry gate is closed and shadow mode is on by default (see
        # core/config.py), so on_error's own retry path here is the shadowed
        # branch, not the tenant/task-allowlisted "Retrying (n/max)" branch;
        # neither is dispatched via AsyncKicker, so no extra commit occurs.
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        first_task = MagicMock(status=TaskStatus.RUNNING, retry_count=0, max_retries=3)
        second_task = MagicMock(
            status=TaskStatus.RETRYING,
            retry_count=1,
            max_retries=3,
            status_message="Retry shadowed (automatic retry disabled)",
        )
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        async def update_once(*args, **kwargs):
            first_task.status = TaskStatus.RETRYING
            first_task.retry_count = 1

        commit_error = OperationalError("commit", {}, Exception("connection lost"))
        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch(
                "worker_template.middleware.state_tracking.get_task_execution",
                side_effect=[first_task, second_task],
            ),
            patch(
                "worker_template.middleware.state_tracking.update_task_status",
                side_effect=update_once,
            ) as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt"),
        ):
            mock_session.commit.side_effect = [commit_error, None]
            await middleware.on_error(msg, result, exc)

        assert mock_maker.call_count == 2
        assert mock_session.commit.call_count == 2
        mock_update.assert_called_once()

    async def test_on_error_retries_increment_for_preexisting_retrying_task(self, middleware):
        # Same shadow-mode-by-default reasoning as the test above: second_task
        # is a distinct object from first_task, simulating that the first
        # commit genuinely rolled back, so the retry-count increment (and the
        # shadowed status write) legitimately happens again on the retried
        # attempt.
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        result = make_result(is_err=True)
        exc = RuntimeError("crash")

        first_task = MagicMock(
            status=TaskStatus.RETRYING,
            retry_count=1,
            max_retries=3,
            status_message="Retrying (1/3)",
        )
        second_task = MagicMock(
            status=TaskStatus.RETRYING,
            retry_count=1,
            max_retries=3,
            status_message="Retrying (1/3)",
        )
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)
        updated_tasks = []

        async def update_status(_session, _task_id, status, **kwargs):
            task = first_task if not updated_tasks else second_task
            task.status = status
            task.status_message = kwargs["status_message"]
            if status == TaskStatus.RETRYING:
                task.retry_count += 1
            updated_tasks.append(task)

        commit_error = OperationalError("commit", {}, Exception("connection lost"))
        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch(
                "worker_template.middleware.state_tracking.get_task_execution",
                side_effect=[first_task, second_task],
            ),
            patch(
                "worker_template.middleware.state_tracking.update_task_status",
                side_effect=update_status,
            ) as mock_update,
            patch("worker_template.middleware.state_tracking.record_task_attempt"),
        ):
            mock_session.commit.side_effect = [commit_error, None]
            await middleware.on_error(msg, result, exc)

        assert mock_update.call_count == 2
        assert second_task.retry_count == 2
        assert second_task.status_message == "Retry shadowed (automatic retry disabled)"

    async def test_pre_execute_raises_after_max_attempts(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        _mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            mock_update.side_effect = OperationalError("stmt", {}, Exception("conn lost"))

            with pytest.raises(OperationalError):
                await middleware.pre_execute(msg)

            assert mock_maker.call_count == DEFAULT_MAX_ATTEMPTS

    async def test_pre_execute_does_not_retry_non_operational_error(self, middleware):
        task_exec_id = uuid4()
        msg = make_message(labels={"task_execution_id": str(task_exec_id)})
        _mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)

        with (
            patch("worker_template.middleware.state_tracking.async_session_maker", mock_maker),
            patch("worker_template.middleware.state_tracking.update_task_status") as mock_update,
        ):
            mock_update.side_effect = ValueError("not a db error")

            with pytest.raises(ValueError, match="not a db error"):
                await middleware.pre_execute(msg)

            assert mock_maker.call_count == 1
