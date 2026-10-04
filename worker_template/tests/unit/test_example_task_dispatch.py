"""Tests for dispatch_example_task: tracked row creation and keyword-form enqueue (mocked DB)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from worker_template.tasks.example_task import dispatch_example_task

_SESSION_MAKER = "worker_template.db.session.async_session_maker"
_CREATE_TASK_EXECUTION = "worker_template.tasks.example_task.create_task_execution"
_EXAMPLE_TASK = "worker_template.tasks.example_task.example_task"
_TASK_NAME = "worker_template.tasks.example_task:example_task"


def make_mock_session():
    """Create a mock async session that supports async context manager."""
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_ctx


def make_task_execution_stub(tenant_id):
    """Create a TaskExecution-like stub with id and tenant_id."""
    stub = MagicMock()
    stub.id = uuid4()
    stub.tenant_id = tenant_id
    return stub


def make_example_task_mock():
    """Create a stand-in for the decorated example_task with task_name and an async kiq."""
    task = MagicMock()
    task.task_name = _TASK_NAME
    task.kiq = AsyncMock()
    return task


class TestDispatchExampleTask:
    async def test_dispatch_creates_pending_row_and_kiqs_by_keyword(self):
        tenant_id = uuid4()
        stub = make_task_execution_stub(tenant_id)
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)
        mock_create = AsyncMock(return_value=stub)
        mock_task = make_example_task_mock()

        with (
            patch(_SESSION_MAKER, mock_maker),
            patch(_CREATE_TASK_EXECUTION, mock_create),
            patch(_EXAMPLE_TASK, mock_task),
        ):
            row, kicked = await dispatch_example_task(
                tenant_id=tenant_id,
                document_url="https://example.com/doc.pdf",
                output_format="docx",
            )

        mock_create.assert_awaited_once_with(mock_session, task_name=_TASK_NAME, tenant_id=tenant_id)
        mock_session.commit.assert_awaited_once()
        mock_task.kiq.assert_awaited_once()
        kiq_call = mock_task.kiq.await_args
        assert kiq_call.args == ()
        assert set(kiq_call.kwargs) == {"raw_input"}
        raw_input = kiq_call.kwargs["raw_input"]
        assert raw_input["task_execution_id"] == stub.id
        assert raw_input["tenant_id"] == tenant_id
        assert raw_input["document_url"] == "https://example.com/doc.pdf"
        assert raw_input["output_format"] == "docx"
        assert row is stub
        assert kicked is mock_task.kiq.return_value

    async def test_dispatch_commits_before_kiq(self):
        tenant_id = uuid4()
        stub = make_task_execution_stub(tenant_id)
        mock_session, mock_ctx = make_mock_session()
        mock_maker = MagicMock(return_value=mock_ctx)
        mock_create = AsyncMock(return_value=stub)
        mock_task = make_example_task_mock()
        calls: list[str] = []
        mock_session.commit.side_effect = lambda: calls.append("commit")
        mock_task.kiq.side_effect = lambda **_: calls.append("kiq")

        with (
            patch(_SESSION_MAKER, mock_maker),
            patch(_CREATE_TASK_EXECUTION, mock_create),
            patch(_EXAMPLE_TASK, mock_task),
        ):
            await dispatch_example_task(
                tenant_id=tenant_id,
                document_url="https://example.com/doc.pdf",
            )

        assert calls == ["commit", "kiq"]
