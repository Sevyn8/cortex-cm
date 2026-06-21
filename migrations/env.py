"""Alembic env.py — Ithina Admin Backend.

Reads DATABASE_URL and DB_SCHEMA from environment. Refuses to run if
either is missing. Sets search_path on the migration connection so
unqualified DDLs land in the configured schema (per D-15 in CLAUDE.md).
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Read DATABASE_URL from environment, fall back to alembic.ini
db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# DB_SCHEMA is required (per D-15: schema name parameterised per environment).
# Refuse to run rather than silently using a default; fail loudly.
db_schema = os.environ.get("DB_SCHEMA")
if not db_schema:
    raise RuntimeError(
        "DB_SCHEMA env var is required. See CLAUDE.md D-15 (schema name "
        "parameterised per environment via DB_SCHEMA)."
    )

# TODO: when ORM models exist (Step 3.1+), set to Base.metadata for
# autogenerate support. For now, raw SQL migrations only.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=db_schema,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=db_schema,
        include_schemas=True,
    )

    with context.begin_transaction():
        # Set search_path INSIDE alembic's transaction so unqualified DDLs
        # resolve to the configured schema. Doing this BEFORE
        # context.begin_transaction implicitly opens a SQLAlchemy 2.x
        # transaction that interferes with alembic's transaction
        # management and silently rolls back the migration. Belt-and-
        # suspenders against role-default search_path drift; the role's
        # default search_path is also set in the local setup procedure.
        connection.execute(text(f'SET search_path TO "{db_schema}", public'))
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Ensure the configured schema exists before Alembic tries to
        # CREATE TABLE alembic_version inside it. Idempotent (no-op if
        # already present, e.g. local where Step 1.4 set it up by hand).
        # Required on a fresh cloud DB because Terraform provisions the
        # database and role, not the schema. See "Cloud-specific
        # differences" in CLAUDE.md. Identifier is double-quote-wrapped
        # for parity with do_run_migrations' search_path SET below.
        await connection.execute(
            text(f'CREATE SCHEMA IF NOT EXISTS "{db_schema}"')
        )
        await connection.commit()
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
