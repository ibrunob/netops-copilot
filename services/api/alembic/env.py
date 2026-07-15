"""Alembic runtime configuration for NetOps Copilot persistence.

The database URL is supplied at execution time by the Make targets or CI. It
is deliberately not recorded in versioned configuration because it contains a
local password. The persistence slice will set ``target_metadata`` when it
introduces SQLAlchemy models; initial revisions must be authored explicitly.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("NETOPS_DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "NETOPS_DATABASE_URL is required. Use `make migrate` or `make test-migrate` "
        "instead of placing a database URL in alembic.ini."
    )

# The persistence implementation owns SQLAlchemy metadata and will replace this
# with its Base.metadata. Keeping it None prevents a premature, empty
# autogenerate revision from appearing before tenant/RLS table design is ready.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database specified by NETOPS_DATABASE_URL."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
