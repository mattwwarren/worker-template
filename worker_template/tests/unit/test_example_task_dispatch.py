"""Tests for dispatch_example_task: TaskExecution creation + kiq threading."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from worker_template.tasks.example_task import dispatch_example_task, example_task


def make_mock_session():
    """Create a mock async session that supports async context manager."""
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_ctx


class TestDispatchExampleTask:
    async def test_creates_pending_row_and_kiqs_with_task_execution_id(self):
        tenant_id = uuid4()
        task_execution_id = uuid4()
        stub_task_execution = MagicMock(id=task_execution_id, tenant_id=tenant_id)
        mock_session, mock_ctx = make_mock_session()
        mock_kicked_task = MagicMock()

        with (
            patch("worker_template.tasks.example_task.db_session.async_session_maker", return_value=mock_ctx),
            patch(
                "worker_template.tasks.example_task.create_task_execution",
                new_callable=AsyncMock,
                return_value=stub_task_execution,
            ) as mock_create,
            patch(
                "worker_template.tasks.example_task.example_task.kiq",
                new_callable=AsyncMock,
                return_value=mock_kicked_task,
            ) as mock_kiq,
        ):
            result = await dispatch_example_task(
                tenant_id=tenant_id,
                document_url="https://example.com/doc.pdf",
                output_format="pdf",
            )

            mock_create.assert_awaited_once_with(
                mock_session,
                task_name=example_task.task_name,
                tenant_id=tenant_id,
            )
            mock_kiq.assert_awaited_once()
            _, kiq_kwargs = mock_kiq.call_args
            raw_input = kiq_kwargs["raw_input"]
            assert raw_input["task_execution_id"] == task_execution_id
            assert raw_input["tenant_id"] == tenant_id
            assert result == (stub_task_execution, mock_kicked_task)

    async def test_commits_before_kiq(self):
        tenant_id = uuid4()
        stub_task_execution = MagicMock(id=uuid4(), tenant_id=tenant_id)
        mock_session, mock_ctx = make_mock_session()

        call_order: list[str] = []
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        async def fake_kiq(**_kwargs):
            call_order.append("kiq")
            return MagicMock()

        with (
            patch("worker_template.tasks.example_task.db_session.async_session_maker", return_value=mock_ctx),
            patch(
                "worker_template.tasks.example_task.create_task_execution",
                new_callable=AsyncMock,
                return_value=stub_task_execution,
            ),
            patch("worker_template.tasks.example_task.example_task.kiq", side_effect=fake_kiq),
        ):
            await dispatch_example_task(
                tenant_id=tenant_id,
                document_url="https://example.com/doc.pdf",
            )

            assert call_order == ["commit", "kiq"]
