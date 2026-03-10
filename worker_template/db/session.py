"""Async SQLAlchemy engine and session factory for the worker."""

import logging
from collections.abc import AsyncGenerator

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from worker_template.core.config import settings
from worker_template.core.logging import get_logging_context

LOGGER = logging.getLogger(__name__)


class PoolConfig(BaseModel, frozen=True):
    """Database connection pool configuration.

    Attributes:
        size: Number of connections to keep in pool
        max_overflow: Max connections beyond pool_size
        timeout: Seconds to wait for available connection
        recycle: Seconds before recycling connection (-1 to disable)
        pre_ping: Test connection validity before use
    """

    size: int = Field(default=5, ge=1, le=100, description="Pool size")
    max_overflow: int = Field(default=10, ge=0, le=100, description="Max overflow")
    timeout: float = Field(default=30.0, ge=1.0, description="Connection timeout")
    recycle: int = Field(default=1800, ge=-1, description="Connection recycle time")
    pre_ping: bool = Field(default=True, description="Enable pre-ping health check")


DEFAULT_POOL_CONFIG = PoolConfig()


def create_db_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool: PoolConfig | None = None,
) -> AsyncEngine:
    """Factory function to create database engine.

    Args:
        database_url: Database connection URL
        echo: Echo SQL statements to logs
        pool: Connection pool configuration (uses defaults if not specified)

    Returns:
        Configured async SQLAlchemy engine
    """
    pool_config = pool or DEFAULT_POOL_CONFIG
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=pool_config.size,
        max_overflow=pool_config.max_overflow,
        pool_timeout=pool_config.timeout,
        pool_recycle=pool_config.recycle,
        pool_pre_ping=pool_config.pre_ping,
    )


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Factory function to create session maker.

    Args:
        engine: Async SQLAlchemy engine to bind sessions to

    Returns:
        Configured async session maker
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# Global engine and session maker.
# Tests replace these with worker-specific instances.
_default_pool = PoolConfig(
    size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    timeout=settings.db_pool_timeout,
    recycle=settings.db_pool_recycle,
    pre_ping=settings.db_pool_pre_ping,
)
engine = create_db_engine(
    settings.database_url,
    echo=settings.sqlalchemy_echo,
    pool=_default_pool,
)
async_session_maker: async_sessionmaker[AsyncSession] = create_session_maker(engine)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Create async database session.

    Yields:
        AsyncSession for database operations
    """
    context = get_logging_context()
    LOGGER.debug("session_created", extra=context)

    async with async_session_maker() as session:
        try:
            yield session
            LOGGER.debug("session_completed", extra=context)
        except Exception:
            LOGGER.warning("session_rollback", extra=context, exc_info=True)
            await session.rollback()
            raise


async def init_db(db_engine: AsyncEngine | None = None) -> None:
    """Test-only helper; production migrations should use Alembic."""
    target_engine = db_engine if db_engine is not None else engine
    async with target_engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
