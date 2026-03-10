"""Tests for db.session: create_db_engine, create_session_maker, PoolConfig."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from worker_template.db.session import (
    DEFAULT_POOL_CONFIG,
    PoolConfig,
    create_db_engine,
    create_session_maker,
    get_session,
)


class TestPoolConfig:
    def test_default_values(self):
        cfg = PoolConfig()

        assert cfg.size == 5
        assert cfg.max_overflow == 10
        assert cfg.timeout == 30.0
        assert cfg.recycle == 1800
        assert cfg.pre_ping is True

    def test_custom_values(self):
        cfg = PoolConfig(size=20, max_overflow=30, timeout=60.0, recycle=900, pre_ping=False)

        assert cfg.size == 20
        assert cfg.max_overflow == 30
        assert cfg.timeout == 60.0
        assert cfg.recycle == 900
        assert cfg.pre_ping is False

    def test_frozen_model(self):
        cfg = PoolConfig()
        with pytest.raises(ValidationError):
            cfg.size = 99  # type: ignore[misc]

    def test_size_minimum_boundary(self):
        cfg = PoolConfig(size=1)
        assert cfg.size == 1

    def test_size_maximum_boundary(self):
        cfg = PoolConfig(size=100)
        assert cfg.size == 100

    def test_size_below_minimum_raises(self):
        with pytest.raises(ValidationError):
            PoolConfig(size=0)

    def test_size_above_maximum_raises(self):
        with pytest.raises(ValidationError):
            PoolConfig(size=101)

    def test_recycle_negative_one_allowed(self):
        cfg = PoolConfig(recycle=-1)
        assert cfg.recycle == -1

    def test_recycle_below_negative_one_raises(self):
        with pytest.raises(ValidationError):
            PoolConfig(recycle=-2)


class TestDefaultPoolConfig:
    def test_is_pool_config_instance(self):
        assert isinstance(DEFAULT_POOL_CONFIG, PoolConfig)


class TestCreateDbEngine:
    @patch("worker_template.db.session.create_async_engine")
    def test_returns_engine(self, mock_create):
        mock_engine = MagicMock(spec=AsyncEngine)
        mock_create.return_value = mock_engine

        result = create_db_engine("sqlite+aiosqlite:///test.db")

        assert result is mock_engine
        mock_create.assert_called_once()

    @patch("worker_template.db.session.create_async_engine")
    def test_uses_default_pool_config(self, mock_create):
        mock_create.return_value = MagicMock(spec=AsyncEngine)

        create_db_engine("sqlite+aiosqlite:///test.db")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_size"] == DEFAULT_POOL_CONFIG.size
        assert call_kwargs["max_overflow"] == DEFAULT_POOL_CONFIG.max_overflow
        assert call_kwargs["pool_timeout"] == DEFAULT_POOL_CONFIG.timeout
        assert call_kwargs["pool_recycle"] == DEFAULT_POOL_CONFIG.recycle
        assert call_kwargs["pool_pre_ping"] == DEFAULT_POOL_CONFIG.pre_ping

    @patch("worker_template.db.session.create_async_engine")
    def test_uses_custom_pool_config(self, mock_create):
        mock_create.return_value = MagicMock(spec=AsyncEngine)
        custom_pool = PoolConfig(size=2, max_overflow=5, timeout=10.0, recycle=600, pre_ping=False)

        create_db_engine("sqlite+aiosqlite:///test.db", pool=custom_pool)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_size"] == 2
        assert call_kwargs["max_overflow"] == 5
        assert call_kwargs["pool_timeout"] == 10.0
        assert call_kwargs["pool_recycle"] == 600
        assert call_kwargs["pool_pre_ping"] is False

    @patch("worker_template.db.session.create_async_engine")
    def test_echo_parameter(self, mock_create):
        mock_create.return_value = MagicMock(spec=AsyncEngine)

        create_db_engine("sqlite+aiosqlite:///test.db", echo=True)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["echo"] is True

    @patch("worker_template.db.session.create_async_engine")
    def test_echo_defaults_false(self, mock_create):
        mock_create.return_value = MagicMock(spec=AsyncEngine)

        create_db_engine("sqlite+aiosqlite:///test.db")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["echo"] is False


class TestCreateSessionMaker:
    def test_returns_session_maker(self):
        mock_engine = MagicMock(spec=AsyncEngine)

        result = create_session_maker(mock_engine)

        assert isinstance(result, async_sessionmaker)

    def test_session_maker_bound_to_engine(self):
        mock_engine = MagicMock(spec=AsyncEngine)

        maker = create_session_maker(mock_engine)

        # async_sessionmaker stores the bind
        assert maker.kw.get("bind") is mock_engine


class TestGetSession:
    async def test_yields_session(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_maker = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_maker.return_value = mock_ctx

        with patch("worker_template.db.session.async_session_maker", mock_maker):
            gen = get_session()
            session = await gen.__anext__()
            assert session is mock_session
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

    async def test_rollback_on_exception(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_maker = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_maker.return_value = mock_ctx

        with patch("worker_template.db.session.async_session_maker", mock_maker):
            gen = get_session()
            await gen.__anext__()
            # Simulate exception inside the generator
            with pytest.raises(ValueError, match="boom"):
                await gen.athrow(ValueError("boom"))

            mock_session.rollback.assert_called_once()
