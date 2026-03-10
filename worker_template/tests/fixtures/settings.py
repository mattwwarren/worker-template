"""Settings test fixtures."""

import pytest

from worker_template.core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Provide a Settings instance configured for testing."""
    return Settings(
        app_name="worker_template_test",
        environment="test",
        LOG_LEVEL="debug",
        DATABASE_URL="postgresql+asyncpg://app:app@localhost:5432/app_test",
        RABBITMQ_URL="amqp://guest:guest@localhost:5672/",
        REDIS_URL="redis://localhost:6379/0",
    )
