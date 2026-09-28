"""Tests for worker startup/shutdown lifecycle hooks."""

from unittest.mock import AsyncMock, MagicMock, patch

from taskiq.state import TaskiqState

from worker_template.core.config import Settings, settings
from worker_template.worker import on_shutdown, on_startup


class TestOnStartup:
    @patch("worker_template.worker.init_emitter", new_callable=AsyncMock)
    @patch("worker_template.worker.create_session_maker")
    @patch("worker_template.worker.create_db_engine")
    async def test_creates_engine_and_session_maker_on_state(
        self, mock_create_engine, mock_create_session_maker, mock_init_emitter
    ):
        engine = MagicMock()
        session_maker = MagicMock()
        mock_create_engine.return_value = engine
        mock_create_session_maker.return_value = session_maker
        state = TaskiqState()

        await on_startup(state)

        mock_create_engine.assert_called_once_with(settings.database_url)
        mock_create_session_maker.assert_called_once_with(engine)
        assert state.engine is engine
        assert state.session_maker is session_maker
        mock_init_emitter.assert_awaited_once_with(settings.redis_url)

    @patch("worker_template.worker.create_session_maker", MagicMock())
    @patch("worker_template.worker.init_emitter", new_callable=AsyncMock)
    @patch("worker_template.worker.create_db_engine")
    async def test_skips_emitter_init_when_no_redis_url(self, mock_create_engine, mock_init_emitter):
        fake_settings = MagicMock(
            redis_url="",
            database_url="postgresql+psycopg://user:pass@localhost/db",
            validate_config=MagicMock(return_value=[]),
        )
        state = TaskiqState()

        with patch("worker_template.worker.settings", fake_settings):
            await on_startup(state)

        mock_init_emitter.assert_not_called()
        mock_create_engine.assert_called_once_with(fake_settings.database_url)

    @patch("worker_template.worker.init_emitter", AsyncMock())
    @patch("worker_template.worker.create_session_maker", MagicMock())
    @patch("worker_template.worker.create_db_engine", MagicMock())
    async def test_logs_config_warnings(self, caplog):
        prod_settings = Settings(
            environment="production",
            SQLALCHEMY_ECHO=True,
            WORKER_CONCURRENCY=1,
        )
        state = TaskiqState()

        with patch("worker_template.worker.settings", prod_settings), caplog.at_level("WARNING"):
            await on_startup(state)

        warning_records = [r for r in caplog.records if r.getMessage() == "config_warning"]
        assert len(warning_records) >= 2


class TestOnShutdown:
    @patch("worker_template.worker.close_emitter", new_callable=AsyncMock)
    async def test_disposes_engine_and_closes_emitter(self, mock_close_emitter):
        state = TaskiqState()
        state.engine = AsyncMock()

        await on_shutdown(state)

        state.engine.dispose.assert_awaited_once()
        mock_close_emitter.assert_awaited_once()

    @patch("worker_template.worker.close_emitter", new_callable=AsyncMock)
    async def test_noop_when_no_engine_on_state(self, mock_close_emitter):
        state = TaskiqState()

        await on_shutdown(state)

        mock_close_emitter.assert_awaited_once()
