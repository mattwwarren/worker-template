"""Worker entrypoint for TaskIQ.

Run with: taskiq worker worker_template.worker:broker
"""

from __future__ import annotations

import logging

from worker_template.broker import broker
from worker_template.core.config import settings
from worker_template.db.session import create_db_engine, create_session_maker
from worker_template.middleware import register_middleware
from worker_template.realtime.emitter import close_emitter, init_emitter

# Import tasks to trigger @broker.task registration
from worker_template.tasks import example_task as _example_task  # noqa: F401

LOGGER = logging.getLogger(__name__)

# Register middleware pipeline
register_middleware(broker)


@broker.on_event("startup")  # type: ignore[arg-type]
async def on_startup(state: object) -> None:
    """Initialize resources on worker startup."""
    warnings = settings.validate_config()
    for warning in warnings:
        LOGGER.warning("config_warning", extra={"detail": warning})

    # Create database engine and session maker, store in broker state
    engine = create_db_engine(settings.database_url)
    session_maker = create_session_maker(engine)

    # Store on broker state for access in tasks
    state.engine = engine  # type: ignore[attr-defined]
    state.session_maker = session_maker  # type: ignore[attr-defined]

    # Initialize real-time event emitter
    if settings.redis_url:
        await init_emitter(settings.redis_url)

    LOGGER.info(
        "worker_started",
        extra={
            "app_name": settings.app_name,
            "environment": settings.environment,
            "concurrency": settings.worker_concurrency,
        },
    )


@broker.on_event("shutdown")  # type: ignore[arg-type]
async def on_shutdown(state: object) -> None:
    """Clean up resources on worker shutdown."""
    engine = getattr(state, "engine", None)
    if engine is not None:
        await engine.dispose()
    await close_emitter()
    LOGGER.info("worker_shutdown")
