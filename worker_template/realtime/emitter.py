"""Write-only Socket.IO emitter for publishing events via Redis pub/sub.

The worker creates an AsyncServer with AsyncRedisManager but never
accepts connections — it only emits events. The shared Redis manager
ensures emits reach the FastAPI server's connected clients.
"""

from __future__ import annotations

import logging
from uuid import UUID

import socketio
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)

_sio: socketio.AsyncServer | None = None


async def init_emitter(redis_url: str) -> None:
    """Initialize the write-only Socket.IO emitter.

    Args:
        redis_url: Redis connection URL for AsyncRedisManager.
    """
    global _sio  # noqa: PLW0603
    mgr = socketio.AsyncRedisManager(redis_url, write_only=True)
    _sio = socketio.AsyncServer(async_mode="asgi", client_manager=mgr)
    safe_url = redis_url.rsplit("@", maxsplit=1)[-1]
    LOGGER.info("realtime_emitter_initialized", extra={"redis_url": safe_url})


async def close_emitter() -> None:
    """Shut down the emitter and clean up resources."""
    global _sio  # noqa: PLW0603
    if _sio is not None:
        mgr = _sio.manager
        if hasattr(mgr, "disconnect"):
            try:
                await mgr.disconnect()
            except Exception:
                LOGGER.exception("realtime_emitter_close_error")
        _sio = None
        LOGGER.info("realtime_emitter_closed")


async def emit_task_event(tenant_id: UUID, event: str, data: BaseModel) -> None:
    """Emit a task event to the org room via Redis pub/sub.

    This is fire-and-forget: errors are logged but never raised,
    so a failed emit can never crash the task.

    Args:
        tenant_id: Organization UUID — determines the target room.
        event: Event name constant from contracts.py.
        data: Pydantic model payload.
    """
    if _sio is None:
        LOGGER.debug("realtime_emitter_not_initialized", extra={"event": event})
        return

    room = f"org:{tenant_id}"
    payload = data.model_dump(mode="json")
    try:
        await _sio.emit(event, payload, room=room)
    except Exception:
        LOGGER.warning("realtime_emit_failed", extra={"event": event, "room": room}, exc_info=True)
