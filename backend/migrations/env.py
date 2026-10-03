"""Alembic environment. URL comes from app.core.config (root .env); metadata from all app models."""
import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.operations import ops
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (auto-imports every model module)
from app.core.config import get_settings
from app.core.db import Base, _async_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _sequences_for_initial_revision(ctx, revision, directives) -> None:
    """Autogenerate does not emit standalone Sequences (incident/experiment numbers, event seq). Add them to the
    initial revision so the migrated schema matches `Base.metadata.create_all`."""
    script = directives[0]
    if script.upgrade_ops.is_empty() or script.head not in (None, "base"):
        return
    for name in sorted(Base.metadata._sequences):
        script.upgrade_ops.ops.insert(0, ops.ExecuteSQLOp(f"CREATE SEQUENCE IF NOT EXISTS {name}"))
        script.downgrade_ops.ops.append(ops.ExecuteSQLOp(f"DROP SEQUENCE IF EXISTS {name}"))


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True,
        process_revision_directives=_sequences_for_initial_revision,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_async_url(_url()), poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
