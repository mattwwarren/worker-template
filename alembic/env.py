"""Alembic environment configuration for async migrations."""

from asyncio.runners import run
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from alembic import context
from worker_template.core.config import settings
from worker_template.db import base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if config.get_main_option("sqlalchemy.url") in {
    "",
    "driver://user:pass@localhost/dbname",
}:
    config.set_main_option("sqlalchemy.url", settings.database_url)


target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        if isinstance(connection, Engine):
            with connection.connect() as conn:
                do_run_migrations(conn)
        else:
            do_run_migrations(connection)
        return

    section = config.get_section(config.config_ini_section)
    if section is None:
        msg = f"Missing alembic config section: {config.config_ini_section}"
        raise RuntimeError(msg)
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async def run_async_migrations() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
