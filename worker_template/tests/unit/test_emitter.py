"""Tests for the write-only Socket.IO emitter."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from worker_template.realtime import emitter as emitter_mod
from worker_template.realtime.contracts import TASK_STATUS_CHANGED, TaskStatusEvent


@pytest.fixture(autouse=True)
def _reset_emitter():
    """Reset emitter module state between tests."""
    emitter_mod._sio = None
    yield
    emitter_mod._sio = None


class TestInitEmitter:
    async def test_creates_server_with_write_only_manager(self):
        """init_emitter creates an AsyncServer with a write-only AsyncRedisManager."""
        with patch("worker_template.realtime.emitter.socketio") as mock_sio_module:
            mock_mgr = MagicMock()
            mock_sio_module.AsyncRedisManager.return_value = mock_mgr
            mock_server = MagicMock()
            mock_sio_module.AsyncServer.return_value = mock_server

            await emitter_mod.init_emitter("redis://localhost:6379/0")

            mock_sio_module.AsyncRedisManager.assert_called_once_with(
                "redis://localhost:6379/0", write_only=True
            )
            mock_sio_module.AsyncServer.assert_called_once_with(
                async_mode="asgi", client_manager=mock_mgr
            )
            assert emitter_mod._sio is mock_server


class TestCloseEmitter:
    async def test_disconnects_manager_and_resets_state(self):
        """close_emitter disconnects the manager and sets _sio to None."""
        mock_mgr = MagicMock()
        mock_mgr.disconnect = AsyncMock()
        mock_server = MagicMock()
        mock_server.manager = mock_mgr
        emitter_mod._sio = mock_server

        await emitter_mod.close_emitter()

        mock_mgr.disconnect.assert_awaited_once()
        assert emitter_mod._sio is None

    async def test_noop_when_not_initialized(self):
        """close_emitter is a no-op when emitter was never initialized."""
        await emitter_mod.close_emitter()  # Should not raise
        assert emitter_mod._sio is None

    async def test_handles_disconnect_exception(self):
        """close_emitter logs but does not raise on manager disconnect error."""
        mock_mgr = MagicMock()
        mock_mgr.disconnect = AsyncMock(side_effect=ConnectionError("Redis gone"))
        mock_server = MagicMock()
        mock_server.manager = mock_mgr
        emitter_mod._sio = mock_server

        await emitter_mod.close_emitter()

        assert emitter_mod._sio is None

    async def test_handles_manager_without_disconnect(self):
        """close_emitter handles a manager that has no disconnect method."""
        mock_mgr = object()  # No disconnect attribute
        mock_server = MagicMock()
        mock_server.manager = mock_mgr
        emitter_mod._sio = mock_server

        await emitter_mod.close_emitter()

        assert emitter_mod._sio is None


class TestEmitTaskEvent:
    async def test_emits_to_correct_room(self):
        """emit_task_event emits to org:{tenant_id} room with JSON payload."""
        mock_sio = MagicMock()
        mock_sio.emit = AsyncMock()
        emitter_mod._sio = mock_sio

        tenant_id = uuid4()
        event_data = TaskStatusEvent(
            task_id=uuid4(),
            task_name="test_task",
            status="RUNNING",
            tenant_id=tenant_id,
        )

        await emitter_mod.emit_task_event(tenant_id, TASK_STATUS_CHANGED, event_data)

        mock_sio.emit.assert_awaited_once()
        call_args = mock_sio.emit.call_args
        assert call_args[0][0] == TASK_STATUS_CHANGED
        assert call_args[0][1] == event_data.model_dump(mode="json")
        assert call_args[1]["room"] == f"org:{tenant_id}"

    async def test_noop_when_not_initialized(self):
        """emit_task_event is a no-op when emitter not initialized."""
        tenant_id = uuid4()
        event_data = TaskStatusEvent(
            task_id=uuid4(),
            task_name="test_task",
            status="RUNNING",
            tenant_id=tenant_id,
        )

        # Should not raise
        await emitter_mod.emit_task_event(tenant_id, "test", event_data)

    async def test_handles_emit_exception(self):
        """emit_task_event logs but does not raise on emit error."""
        mock_sio = MagicMock()
        mock_sio.emit = AsyncMock(side_effect=ConnectionError("Redis down"))
        emitter_mod._sio = mock_sio

        tenant_id = uuid4()
        event_data = TaskStatusEvent(
            task_id=uuid4(),
            task_name="test_task",
            status="RUNNING",
            tenant_id=tenant_id,
        )

        # Should NOT raise
        await emitter_mod.emit_task_event(tenant_id, "test_event", event_data)
