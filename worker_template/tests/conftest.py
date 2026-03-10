"""Pytest fixtures for worker template testing."""

import os

# Set TASKIQ_ENV before any worker imports
os.environ["TASKIQ_ENV"] = "test"

import asyncio
import contextlib
import socket
from collections.abc import Generator
from pathlib import Path

import alembic.command as alembic_command
import filelock
import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from pytest_docker.plugin import DockerComposeExecutor, Services
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from taskiq import InMemoryBroker

from worker_template.db import session as db_session
from worker_template.db.session import create_session_maker

POSTGRES_PORT = 5432
SOCKET_TIMEOUT_SECONDS = 1
DOCKER_TIMEOUT_SECONDS = 30.0
DOCKER_PAUSE_SECONDS = 0.5


@pytest.fixture(scope="session")
def docker_compose_file() -> str:
    """Path to the test Docker Compose file."""
    return str(Path(__file__).parent.parent.parent / "tests" / "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name(
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """Generate a consistent Docker project name shared across all xdist workers."""
    root_tmp = tmp_path_factory.getbasetemp().parent
    lock_file = root_tmp / "docker_project.lock"
    name_file = root_tmp / "docker_project_name.txt"
    with filelock.FileLock(lock_file):
        if name_file.exists():
            return name_file.read_text().strip()
        project_name = f"pytest_{root_tmp.name}"
        name_file.write_text(project_name)
        return project_name


@pytest.fixture(scope="session")
def docker_services(
    docker_compose_command: str,
    docker_compose_file: str,
    docker_compose_project_name: str,
    docker_setup: str,
    docker_cleanup: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Services]:
    """Start Docker Compose services with xdist-safe coordination."""
    docker_compose = DockerComposeExecutor(
        docker_compose_command,
        docker_compose_file,
        docker_compose_project_name,
    )
    root_tmp = tmp_path_factory.getbasetemp().parent
    lock_file = root_tmp / "docker_startup.lock"
    ready_file = root_tmp / "docker_ready.txt"
    refcount_file = root_tmp / "docker_refcount.txt"

    with filelock.FileLock(lock_file):
        count = int(refcount_file.read_text()) if refcount_file.exists() else 0
        count += 1
        refcount_file.write_text(str(count))
        if not ready_file.exists():
            if docker_setup:
                setup_commands = [docker_setup] if isinstance(docker_setup, str) else docker_setup
                for cmd in setup_commands:
                    docker_compose.execute(cmd)
            ready_file.write_text("ready")

    yield Services(docker_compose)

    with filelock.FileLock(lock_file):
        count = int(refcount_file.read_text()) if refcount_file.exists() else 1
        count -= 1
        refcount_file.write_text(str(count))
        if count == 0:
            if docker_cleanup:
                cleanup_commands = [docker_cleanup] if isinstance(docker_cleanup, str) else docker_cleanup
                for cmd in cleanup_commands:
                    docker_compose.execute(cmd)
            ready_file.unlink(missing_ok=True)
            refcount_file.unlink(missing_ok=True)


def get_worker_database_name(worker_id: str) -> str:
    """Return database name for xdist worker."""
    if worker_id == "master" or not worker_id:
        return "app_test"
    return f"app_test_{worker_id}"


def create_database_if_not_exists(host: str, port: int, db_name: str) -> None:
    """Create database using sync psycopg connection to postgres database."""
    conn_str = f"host={host} port={port} user=app password=app dbname=postgres"
    with (
        psycopg.connect(conn_str, autocommit=True) as conn,
        contextlib.suppress(psycopg.errors.DuplicateDatabase),
    ):
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


@pytest.fixture(scope="session")
def database_url(
    docker_ip: str,
    docker_services: Services,
    worker_id: str,
) -> str:
    """Get database URL with per-worker isolation and shared Docker container."""
    port = docker_services.port_for("postgres", POSTGRES_PORT)

    def is_responsive() -> bool:
        try:
            socket.create_connection(
                (docker_ip, port),
                timeout=SOCKET_TIMEOUT_SECONDS,
            ).close()
        except OSError:
            return False
        return True

    docker_services.wait_until_responsive(
        timeout=DOCKER_TIMEOUT_SECONDS,
        pause=DOCKER_PAUSE_SECONDS,
        check=is_responsive,
    )

    db_name = get_worker_database_name(worker_id)
    create_database_if_not_exists(docker_ip, port, db_name)
    url = f"postgresql+asyncpg://app:app@{docker_ip}:{port}/{db_name}"
    os.environ["DATABASE_URL"] = url
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    """Alembic config pointing at the test database."""
    project_root = Path(__file__).parent.parent.parent
    alembic_ini_path = project_root / "alembic.ini"
    config = Config(str(alembic_ini_path))
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("postgresql+asyncpg", "postgresql+psycopg"),
    )
    return config


@pytest.fixture(scope="session")
def alembic_engine(database_url: str) -> Generator[Engine]:
    """Sync engine for running Alembic migrations."""
    url = make_url(database_url)
    if url.drivername.endswith("asyncpg"):
        url = url.set(drivername=url.drivername.replace("asyncpg", "psycopg"))
    engine = create_engine(url)
    yield engine
    engine.dispose()


def run_migrations(config: Config, engine: Engine) -> None:
    """Run Alembic migrations to head."""
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        try:
            alembic_command.upgrade(config, "head")
        finally:
            config.attributes.pop("connection", None)
        connection.commit()


async def truncate_tables(
    engine: AsyncEngine,
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Truncate all model tables, running migrations first if needed."""
    table_names = [table.name for table in SQLModel.metadata.sorted_tables]
    if not table_names:
        return
    async with engine.begin() as connection:
        result = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN :names").bindparams(
                bindparam("names", expanding=True)
            ),
            {"names": table_names},
        )
        existing = [row[0] for row in result.fetchall()]
    if not existing:
        await asyncio.to_thread(run_migrations, alembic_config, alembic_engine)
        async with engine.begin() as connection:
            result = await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN :names").bindparams(
                    bindparam("names", expanding=True)
                ),
                {"names": table_names},
            )
            existing = [row[0] for row in result.fetchall()]
    if not existing:
        return
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE " + ", ".join(existing) + " RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
async def engine(
    database_url: str,
    alembic_config: Config,
    alembic_engine: Engine,
) -> AsyncEngine:
    """Create async database engine for test session."""
    await asyncio.to_thread(run_migrations, alembic_config, alembic_engine)
    test_engine = create_async_engine(database_url, poolclass=NullPool)
    db_session.engine = test_engine
    db_session.async_session_maker = create_session_maker(test_engine)
    yield test_engine  # type: ignore[misc]
    await truncate_tables(test_engine, alembic_config, alembic_engine)
    await test_engine.dispose()


@pytest.fixture
def session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide a session maker bound to the test engine."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_maker: async_sessionmaker[AsyncSession],
    reset_db: None,
) -> AsyncSession:
    """Provide a database session for tests that need direct database access."""
    async with session_maker() as session:
        yield session  # type: ignore[misc]


@pytest.fixture(autouse=True)
async def reset_db(
    engine: AsyncEngine,
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Truncate all tables before each test."""
    await truncate_tables(engine, alembic_config, alembic_engine)


@pytest.fixture(scope="session")
def test_broker() -> InMemoryBroker:
    """Provide InMemoryBroker for tests."""
    from worker_template.broker import broker

    assert isinstance(broker, InMemoryBroker)
    return broker
