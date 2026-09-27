"""Integration test: dispatch_example_task runs PENDING -> RUNNING -> COMPLETED
through the real broker + middleware pipeline + database."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import worker_template.worker  # noqa: F401  (registers middleware pipeline on the shared broker)
from worker_template.models.task_execution import TaskStatus
from worker_template.realtime.contracts import TASK_COMPLETED, TASK_STATUS_CHANGED
from worker_template.services.task_execution_service import get_task_execution
from worker_template.tasks.example_task import dispatch_example_task


@pytest.mark.integration
async def test_dispatch_example_task_runs_pending_to_completed(session: AsyncSession, test_broker):
    with patch(
        "worker_template.middleware.state_tracking.emit_task_event",
        new_callable=AsyncMock,
    ) as mock_emit:
        task_execution, kicked = await dispatch_example_task(
            tenant_id=uuid4(),
            document_url="https://example.com/doc.pdf",
            output_format="pdf",
        )

        assert task_execution.status == TaskStatus.PENDING

        result = await kicked.wait_result()
        assert result.is_err is False

        updated = await get_task_execution(session, task_execution.id)
        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        assert updated.started_at is not None
        assert updated.completed_at is not None
        assert updated.result_url is not None

        assert mock_emit.await_count >= 2
        event_names = [call.args[1] for call in mock_emit.await_args_list]
        assert TASK_STATUS_CHANGED in event_names
        assert TASK_COMPLETED in event_names
        assert event_names.index(TASK_STATUS_CHANGED) < event_names.index(TASK_COMPLETED)
