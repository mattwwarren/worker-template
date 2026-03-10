"""Tests for worker configuration."""

import pytest

from worker_template.core.config import Settings


class TestSettings:
    def test_default_settings(self):
        s = Settings()
        assert s.app_name == "worker_template"
        assert s.worker_concurrency == 10
        assert s.health_server_port == 8080
        assert s.task_max_retries == 3

    def test_validate_rabbitmq_url_invalid(self):
        with pytest.raises(ValueError, match="amqp://"):
            Settings(RABBITMQ_URL="http://invalid")

    def test_validate_redis_url_invalid(self):
        with pytest.raises(ValueError, match="redis://"):
            Settings(REDIS_URL="http://invalid")

    def test_validate_config_success(self):
        s = Settings()
        warnings = s.validate_config()
        assert isinstance(warnings, list)

    def test_validate_config_production_warnings(self):
        s = Settings(
            environment="production",
            SQLALCHEMY_ECHO=True,
            WORKER_CONCURRENCY=1,
        )
        warnings = s.validate_config()
        assert len(warnings) >= 2
