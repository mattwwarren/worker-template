"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PRODUCTION_CONCURRENCY = 2


class ConfigurationError(ValueError):
    """Raised when configuration validation fails.

    Inherits from ValueError for semantic clarity - this represents
    invalid configuration values.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "worker_template"
    environment: str = "local"
    log_level: str = Field(default="debug", alias="LOG_LEVEL")
    database_url: str = Field(
        default="postgresql+asyncpg://app:app@localhost:5432/app",
        alias="DATABASE_URL",
    )
    sqlalchemy_echo: bool = Field(default=False, alias="SQLALCHEMY_ECHO")
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")

    # Database pool configuration
    db_pool_size: int = Field(
        default=5,
        ge=1,
        le=100,
        alias="DB_POOL_SIZE",
        description="Maximum number of connections to maintain in the pool",
    )
    db_max_overflow: int = Field(
        default=10,
        ge=0,
        le=100,
        alias="DB_MAX_OVERFLOW",
        description="Maximum number of connections to create beyond pool_size",
    )
    db_pool_timeout: int = Field(
        default=30,
        ge=1,
        alias="DB_POOL_TIMEOUT",
        description="Seconds to wait before giving up on getting a connection from pool",
    )
    db_pool_recycle: int = Field(
        default=3600,
        ge=-1,
        alias="DB_POOL_RECYCLE",
        description="Seconds after which to recycle connections (default: 1 hour, -1 to disable)",
    )
    db_pool_pre_ping: bool = Field(
        default=True,
        alias="DB_POOL_PRE_PING",
        description="Test connections for liveness before using them",
    )

    # RabbitMQ broker configuration
    rabbitmq_url: str = Field(
        default="amqp://guest:guest@localhost:5672/",
        alias="RABBITMQ_URL",
        description="RabbitMQ AMQP connection URL",
    )
    rabbitmq_queue_name: str = Field(
        default="worker_tasks",
        alias="RABBITMQ_QUEUE_NAME",
        description="Name of the RabbitMQ queue for task messages",
    )
    rabbitmq_max_priority: int = Field(
        default=10,
        ge=0,
        le=255,
        alias="RABBITMQ_MAX_PRIORITY",
        description="Maximum priority level for RabbitMQ messages (0-255)",
    )

    # Redis result backend configuration
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
        description="Redis connection URL for task result backend",
    )

    # Worker configuration
    worker_concurrency: int = Field(
        default=10,
        ge=1,
        le=1000,
        alias="WORKER_CONCURRENCY",
        description="Number of concurrent task workers",
    )
    health_server_port: int = Field(
        default=8080,
        ge=1024,
        le=65535,
        alias="HEALTH_SERVER_PORT",
        description="Port for the health check HTTP server",
    )

    # Task execution configuration
    task_default_timeout_seconds: int = Field(
        default=3600,
        ge=1,
        alias="TASK_DEFAULT_TIMEOUT_SECONDS",
        description="Default timeout for task execution in seconds (1 hour)",
    )
    task_max_retries: int = Field(
        default=3,
        ge=0,
        le=100,
        alias="TASK_MAX_RETRIES",
        description="Default maximum retry attempts for failed tasks",
    )

    # Multi-tenancy
    enforce_tenant_isolation: bool = Field(
        default=True,
        alias="ENFORCE_TENANT_ISOLATION",
        description="Enable tenant isolation via labels and DB filtering",
    )

    @field_validator("rabbitmq_url")
    @classmethod
    def validate_rabbitmq_url(cls, value: str) -> str:
        """Validate RabbitMQ URL has correct scheme."""
        if not value.startswith(("amqp://", "amqps://")):
            error_msg = "RABBITMQ_URL must start with amqp:// or amqps://"
            raise ValueError(error_msg)
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        """Validate Redis URL has correct scheme."""
        if not value.startswith(("redis://", "rediss://")):
            error_msg = "REDIS_URL must start with redis:// or rediss://"
            raise ValueError(error_msg)
        return value

    def _validate_broker_config(self, errors: list[str]) -> None:
        """Validate broker configuration."""
        if not self.rabbitmq_url:
            errors.append("RABBITMQ_URL is required")
        if not self.redis_url:
            errors.append("REDIS_URL is required")

    def _validate_production_config(self, warnings: list[str]) -> None:
        """Validate production environment configuration."""
        if self.environment != "production":
            return
        if self.sqlalchemy_echo:
            warnings.append("SQLALCHEMY_ECHO=true in production (verbose SQL logging)")
        if self.worker_concurrency < MIN_PRODUCTION_CONCURRENCY:
            warnings.append("WORKER_CONCURRENCY is 1 in production (single worker)")

    def validate_config(self) -> list[str]:
        """Validate configuration for production readiness.

        Returns:
            List of warning messages for non-critical issues

        Raises:
            ConfigurationError: If critical configuration is missing or invalid
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self.database_url:
            errors.append("DATABASE_URL is required")

        self._validate_broker_config(errors)
        self._validate_production_config(warnings)

        if errors:
            error_summary = "; ".join(errors)
            error_msg = f"Configuration errors: {error_summary}"
            raise ConfigurationError(error_msg)

        return warnings


settings = Settings()
