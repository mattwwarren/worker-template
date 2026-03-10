"""Tests for task_execution_service with mocked AsyncSession."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from worker_template.models.task_execution import TaskExecution, TaskStatus
from worker_template.services.task_execution_service import (
    _TERMINAL_STATUSES,
    create_task_execution,
    get_child_tasks,
    get_task_execution,
    list_task_executions,
    update_task_status,
)


def make_mock_session():
    """Create a mock AsyncSession."""
    return AsyncMock()


def make_task_execution(
    task_name="test_task",
    status=TaskStatus.PENDING,
    tenant_id=None,
    retry_count=0,
    max_retries=3,
    started_at=None,
    completed_at=None,
):
    """Create a TaskExecution-like mock."""
    task = MagicMock(spec=TaskExecution)
    task.id = uuid4()
    task.task_name = task_name
    task.status = status
    task.tenant_id = tenant_id or uuid4()
    task.retry_count = retry_count
    task.max_retries = max_retries
    task.started_at = started_at
    task.completed_at = completed_at
    task.status_message = None
    task.error_detail = None
    task.result_url = None
    task.completed_steps = 0
    return task


class TestCreateTaskExecution:
    async def test_creates_and_returns_task(self):
        session = make_mock_session()
        tenant_id = uuid4()

        # Patch the TaskExecution constructor so we can verify what's passed
        with patch("worker_template.services.task_execution_service.TaskExecution") as mock_te:
            mock_task = MagicMock()
            mock_task.id = uuid4()
            mock_te.return_value = mock_task

            result = await create_task_execution(
                session,
                task_name="process_doc",
                tenant_id=tenant_id,
            )

            mock_te.assert_called_once_with(
                task_name="process_doc",
                tenant_id=tenant_id,
                config_snapshot=None,
                parent_task_id=None,
                max_retries=3,
                total_steps=None,
            )
            session.add.assert_called_once_with(mock_task)
            session.flush.assert_called_once()
            session.refresh.assert_called_once_with(mock_task)
            assert result is mock_task

    async def test_creates_with_optional_fields(self):
        session = make_mock_session()
        tenant_id = uuid4()
        parent_id = uuid4()
        config = {"key": "value"}

        with patch("worker_template.services.task_execution_service.TaskExecution") as mock_te:
            mock_task = MagicMock()
            mock_task.id = uuid4()
            mock_te.return_value = mock_task

            result = await create_task_execution(
                session,
                task_name="child_task",
                tenant_id=tenant_id,
                config_snapshot=config,
                parent_task_id=parent_id,
                max_retries=5,
                total_steps=10,
            )

            mock_te.assert_called_once_with(
                task_name="child_task",
                tenant_id=tenant_id,
                config_snapshot=config,
                parent_task_id=parent_id,
                max_retries=5,
                total_steps=10,
            )
            assert result is mock_task


class TestGetTaskExecution:
    async def test_returns_task_when_found(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        session.execute.return_value = mock_result

        result = await get_task_execution(session, task_id)

        assert result is mock_task
        session.execute.assert_called_once()

    async def test_returns_none_when_not_found(self):
        session = make_mock_session()
        task_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await get_task_execution(session, task_id)

        assert result is None

    async def test_filters_by_tenant_id(self):
        session = make_mock_session()
        task_id = uuid4()
        tenant_id = uuid4()
        mock_task = make_task_execution()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        session.execute.return_value = mock_result

        result = await get_task_execution(session, task_id, tenant_id=tenant_id)

        assert result is mock_task
        session.execute.assert_called_once()


class TestUpdateTaskStatus:
    async def test_updates_status(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution(status=TaskStatus.PENDING)

        # Mock get_task_execution to return our mock task
        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            result = await update_task_status(session, task_id, TaskStatus.RUNNING)

            assert mock_task.status == TaskStatus.RUNNING
            session.add.assert_called_once_with(mock_task)
            session.flush.assert_called_once()
            session.refresh.assert_called_once_with(mock_task)
            assert result is mock_task

    async def test_returns_none_when_task_not_found(self):
        session = make_mock_session()
        task_id = uuid4()

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=None,
        ):
            result = await update_task_status(session, task_id, TaskStatus.RUNNING)

            assert result is None
            session.add.assert_not_called()

    async def test_sets_started_at_on_running(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution(status=TaskStatus.PENDING, started_at=None)

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            await update_task_status(session, task_id, TaskStatus.RUNNING)

            assert mock_task.started_at is not None
            assert isinstance(mock_task.started_at, datetime)

    async def test_does_not_overwrite_started_at(self):
        session = make_mock_session()
        task_id = uuid4()
        original_started = datetime(2024, 1, 1)
        mock_task = make_task_execution(status=TaskStatus.RUNNING, started_at=original_started)

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            await update_task_status(session, task_id, TaskStatus.RUNNING)

            assert mock_task.started_at == original_started

    async def test_sets_completed_at_for_terminal_statuses(self):
        for status in _TERMINAL_STATUSES:
            session = make_mock_session()
            task_id = uuid4()
            mock_task = make_task_execution(status=TaskStatus.RUNNING, completed_at=None)

            with patch(
                "worker_template.services.task_execution_service.get_task_execution",
                return_value=mock_task,
            ):
                await update_task_status(session, task_id, status)

                assert mock_task.completed_at is not None, f"completed_at not set for {status}"

    async def test_increments_retry_count_on_retrying(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution(retry_count=1)

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            await update_task_status(session, task_id, TaskStatus.RETRYING)

            assert mock_task.retry_count == 2

    async def test_sets_optional_fields(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution()

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            await update_task_status(
                session,
                task_id,
                TaskStatus.COMPLETED,
                status_message="All done",
                error_detail="none",
                result_url="s3://bucket/result",
                completed_steps=5,
            )

            assert mock_task.status_message == "All done"
            assert mock_task.error_detail == "none"
            assert mock_task.result_url == "s3://bucket/result"
            assert mock_task.completed_steps == 5

    async def test_does_not_set_optional_fields_when_none(self):
        session = make_mock_session()
        task_id = uuid4()
        mock_task = make_task_execution()
        mock_task.status_message = "original"
        mock_task.error_detail = "original_error"

        with patch(
            "worker_template.services.task_execution_service.get_task_execution",
            return_value=mock_task,
        ):
            await update_task_status(session, task_id, TaskStatus.RUNNING)

            # These should remain unchanged since we passed None (default)
            assert mock_task.status_message == "original"
            assert mock_task.error_detail == "original_error"


class TestListTaskExecutions:
    async def test_returns_tasks_for_tenant(self):
        session = make_mock_session()
        tenant_id = uuid4()
        mock_tasks = [make_task_execution(), make_task_execution()]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_tasks
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        result = await list_task_executions(session, tenant_id)

        assert result == mock_tasks
        session.execute.assert_called_once()

    async def test_returns_empty_list(self):
        session = make_mock_session()
        tenant_id = uuid4()

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        result = await list_task_executions(session, tenant_id)

        assert result == []


class TestGetChildTasks:
    async def test_returns_child_tasks(self):
        session = make_mock_session()
        parent_id = uuid4()
        child_tasks = [make_task_execution(), make_task_execution()]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = child_tasks
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        result = await get_child_tasks(session, parent_id)

        assert result == child_tasks
        session.execute.assert_called_once()

    async def test_returns_empty_for_no_children(self):
        session = make_mock_session()
        parent_id = uuid4()

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        result = await get_child_tasks(session, parent_id)

        assert result == []


class TestTerminalStatuses:
    def test_terminal_statuses_contains_expected(self):
        assert TaskStatus.COMPLETED in _TERMINAL_STATUSES
        assert TaskStatus.FAILED in _TERMINAL_STATUSES
        assert TaskStatus.CANCELLED in _TERMINAL_STATUSES
        assert TaskStatus.PARTIAL in _TERMINAL_STATUSES

    def test_non_terminal_statuses_excluded(self):
        assert TaskStatus.PENDING not in _TERMINAL_STATUSES
        assert TaskStatus.RUNNING not in _TERMINAL_STATUSES
        assert TaskStatus.RETRYING not in _TERMINAL_STATUSES
        assert TaskStatus.QUEUED not in _TERMINAL_STATUSES
